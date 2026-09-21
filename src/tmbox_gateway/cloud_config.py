"""Cloud-only configuration delivery; downloads never mutate operational state."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from .central_sync import CentralRuntimeDownload, CentralRuntimeManifest, CentralSyncError, canonical_runtime_url, wait_for_runtime_change
from .lifecycle import us_meet_id
from .engine import TrafficEngine
from .models import ConnectionState, InteractionMode, unconfigured_session
from .runtime import RuntimePublication, RuntimePublicationError
from .us import USError, validate_package


class CloudConfiguration:
    def __init__(self, application):
        self.app = application
        self.lock = threading.Lock()  # Network work must not hold the traffic lock.
        self.last_checked_at = None
        self.state = "idle"
        self.message = ""
        self.notifications_supported = False
        self._stopping = threading.Event()

    def request_stop(self):
        """Let in-flight network requests finish, but start no new adoption."""
        self._stopping.set()

    @property
    def store(self):
        return self.app.runtime_store

    def status(self):
        selected = self.app.lifecycle.selected()
        return {
            "state": self.state, "message": self.message,
            "current_publication_id": selected.get("publication_id") if selected else None,
            "pending_publication_id": self.store._setting("cloud_pending_id") or None,
            "auto_sync": self.store.cloud_auto_sync_enabled(),
            "last_checked_at": self.last_checked_at,
            "linked": bool(self.store.link_token()),
            "notifications_supported": self.notifications_supported,
        }

    def wait_for_change(self):
        """Wake the background worker on publication; manual checks stay free."""
        if self._stopping.is_set():
            return False
        selected = self.app.lifecycle.selected()
        if not self.notifications_supported or not selected or not self.store.cloud_auto_sync_enabled():
            return False
        token = self.store.link_token()
        if not token:
            return False
        after = self.store._setting("cloud_pending_id") or selected["publication_id"]
        started = time.monotonic()
        result = wait_for_runtime_change(token, self.store.central_url() or self.app.config.central_runtime_url, after)
        # A busy Cloud may release a wait immediately. Fall back to the normal
        # polling interval instead of spinning requests against it.
        return not self._stopping.is_set() and result.wait_supported and (result.publication_id != after or time.monotonic() - started >= 1)

    @staticmethod
    def describe(package):
        if isinstance(package.get('schema'), str) and package['schema'].startswith('trainmeet.us.runtime/'):
            validated = validate_package(package)
            return "us", us_meet_id(validated), validated["publication_id"], validated["name"]
        publication = RuntimePublication.parse(package)
        return "eu", publication.meet_id, publication.publication_id, publication.meet_name

    def connect(self, payload):
        code = re.sub(r"[\s-]", "", str(payload.get("sync_code") or ""))
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise CentralSyncError("Ange den sexsiffriga koden från TrainMeet Cloud.")
        url = canonical_runtime_url(str(payload.get("central_url") or self.store.central_url() or self.app.config.central_runtime_url))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise CentralSyncError("Ange en fullständig Cloud-adress utan inloggningsuppgifter i adressen.")
        with self.lock:
            if self._stopping.is_set():
                raise CentralSyncError("Servern stoppas. Ingen ny config kan aktiveras.")
            before = self.app.lifecycle.selected()
            download = self.app.runtime_fetcher(code, url)
            if not isinstance(download, CentralRuntimeDownload) or not download.link_token:
                raise CentralSyncError("Cloud skickade inget giltigt driftpaket med serverkoppling.")
            self.describe(download.package)  # Validate before replacing a working link.
            with self.app.lifecycle.lock:
                if self.app.lifecycle.selected() != before:
                    raise CentralSyncError("Träffen ändrades under hämtningen. Försök igen.")
                result = self._deliver(download.package, allow_switch=payload.get("confirm_meet_change") is True)
                self.store.save_central_url(url)
                self.store.save_link_token(download.link_token)
                self.store.set_cloud_auto_sync(True)
                if self.store.server_name():
                    self.store.complete_installation()
                self.last_checked_at = datetime.now(timezone.utc).isoformat()
                return {**self.app.runtime_summary(self.app.local_admin()), **result, "linked": bool(download.link_token), "restart_required": False}

    def check(self, *, automatic=False):
        if self._stopping.is_set():
            return {"checked": False, "stopping": True}
        if automatic and not self.store.cloud_auto_sync_enabled():
            return {"checked": False, **self.status()}
        with self.lock:
            token = self.store.link_token()
            if not token:
                if automatic:
                    return {"checked": False, **self.status()}
                raise CentralSyncError("Koppla servern till TrainMeet Cloud först.")
            selected = self.app.lifecycle.selected()
            if not selected:
                raise CentralSyncError("Välj först en träff från Cloud.")
            url = canonical_runtime_url(self.store.central_url() or self.app.config.central_runtime_url)
            try:
                manifest = self.app.linked_runtime_fetcher(token, url, True)
                if self._stopping.is_set():
                    return {"checked": False, "stopping": True}
                if not isinstance(manifest, CentralRuntimeManifest):
                    raise CentralSyncError("Cloud skickade inget versionsbesked.")
                self.notifications_supported = manifest.wait_supported
                if manifest.publication_id == selected["publication_id"]:
                    with self.app.lifecycle.lock:
                        if self.app.lifecycle.selected() != selected or self.store.link_token() != token:
                            raise CentralSyncError("Cloud-kopplingen ändrades under hämtningen. Försök igen.")
                        # A withdrawn pending publication must not keep the wait
                        # cursor pointing past Cloud's current version and cause
                        # immediate retry loops. Preserve the downloaded archive.
                        self.store._save_setting("cloud_pending_id", "")
                        self.store._save_setting("cloud_pending_region", "")
                        self.store.clear_pending()
                        self.state, self.message = "current", "Servern använder senaste publicerade config."
                        self.last_checked_at = datetime.now(timezone.utc).isoformat()
                        return {"checked": True, "pending": False, "update_available": False, **self.status()}
                download = self.app.linked_runtime_fetcher(token, url, False)
                if self._stopping.is_set():
                    return {"checked": False, "stopping": True}
                if not isinstance(download, CentralRuntimeDownload):
                    raise CentralSyncError("Cloud skickade inget driftpaket.")
                canonical = json.dumps(download.package, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                if download.package.get("publication_id") != manifest.publication_id:
                    raise CentralSyncError("Cloud-versionen ändrades under hämtningen. Ny kontroll görs automatiskt.")
                if not manifest.package_checksum or hashlib.sha256(canonical).hexdigest() != manifest.package_checksum:
                    raise CentralSyncError("Configens kontrollsumma stämmer inte. Den aktiva träffen är oförändrad.")
                with self.app.lifecycle.lock:
                    if self.app.lifecycle.selected() != selected or self.store.link_token() != token:
                        raise CentralSyncError("Cloud-kopplingen ändrades under hämtningen. Försök igen.")
                    region, meet_id, _, _ = self.describe(download.package)
                    if (region, meet_id) != (selected["region"], selected["meet_id"]):
                        raise CentralSyncError("Cloud skickade en annan träff. Byt träff uttryckligen under Inställningar.")
                    result = self._deliver(download.package)
                self.last_checked_at = datetime.now(timezone.utc).isoformat()
                return {"checked": True, "update_available": result.get("pending", False), **result, **self.status()}
            except (CentralSyncError, RuntimePublicationError, USError) as error:
                self.state, self.message = "error", str(error)
                raise

    def _engine_blockers(self):
        engine = self.app.engine
        if any(c.state != ConnectionState.FREE for c in engine.connections.values()):
            return ["Pågående trafik eller klarering på en sträcka."]
        if any(p.mode != InteractionMode.IDLE for p in engine.panels.values()):
            return ["En operatör arbetar med en TMBox-panel."]
        return []

    def _deliver(self, package, *, allow_switch=False):
        """Called under lifecycle.lock. Stage first; mutate only after all guards."""
        if self._stopping.is_set():
            raise CentralSyncError("Servern stoppas. Ingen ny config kan aktiveras.")
        app = self.app
        region, meet_id, publication_id, name = self.describe(package)
        previous = app.lifecycle.selected()
        ambiguous = bool(not previous and app.lifecycle_error)
        switching = ambiguous or bool(previous and (previous["region"], previous["meet_id"]) != (region, meet_id))
        if switching and not allow_switch:
            raise CentralSyncError("Det här är en annan träff. Bekräfta Byt träff; servern kan bara representera en träff.")
        if app.lifecycle.transition():
            raise CentralSyncError("En tidigare configaktivering avbröts. Servern behöver återställas från säkerhetskopia innan ny aktivering.")
        if region == "us":
            if app.us_store is None:
                raise CentralSyncError("US-lagring saknas på servern.")
            app.us_store.stage_package(package)
        else:
            self.store.stage_pending(package)
            # Validate the engine-level topology before any runtime store is
            # changed. Predictable config errors must not leave a transition.
            TrafficEngine(self.store.publication(publication_id).session_config())
        if previous and previous["publication_id"] == publication_id and not switching:
            self.state, self.message = "current", "Den valda configen används redan."
            return {"pending": False, "publication_id": publication_id, "operating_region": region}
        blockers = self._engine_blockers() if not previous or previous["region"] == "eu" else []
        old_publication = self.store.active()
        us_session = app.us_store.context("config", True)["session"] if app.us_store else None
        if switching and us_session and us_session["status"] != "closed":
            blockers.append("Avsluta den pågående US-körningen innan du byter träff.")
        if old_publication and app.operations_store and region == "eu" and not switching:
            blockers.extend(app.operations_store.config_update_blockers(old_publication, RuntimePublication.parse(package)))
        elif switching and old_publication and app.operations_store:
            # A different traffic system must not bypass EU operational guards.
            blockers.extend(app.operations_store.config_update_blockers(old_publication, old_publication))
            if app.operations_store.clock_status().get("running"):
                blockers.append("Stoppa klockan innan du byter träff.")
        if region == "eu" and app.operations_store and (switching or not old_publication):
            blockers.extend(app.operations_store.start_meet_blockers(self.store.publication(publication_id)))
        if region == "us" and not switching and us_session and us_session["status"] != "closed":
            blockers.extend(app.us_store.config_update_blockers(package))
        if blockers:
            if switching:
                raise CentralSyncError("Träffbytet väntar: " + " ".join(dict.fromkeys(blockers)))
            self.store._save_setting("cloud_pending_id", publication_id)
            self.store._save_setting("cloud_pending_region", region)
            self.state, self.message = "waiting", "Ny config hämtad – väntar: " + " ".join(dict.fromkeys(blockers))
            return {"pending": True, "publication_id": publication_id, "message": self.message}
        ticket = app.lifecycle.begin_transition(region, meet_id, publication_id, meet_name=name, allow_switch=allow_switch)
        # A failure after this point leaves the durable marker in place. Do not
        # claim rollback across separate SQLite connections or accept traffic.
        if switching:
            app.identities.clear_meet_assignments()
            self.store._save_setting("require_scoped_commands", "true")
            if app.us_store:
                app.us_store.deactivate()
        if region == "eu":
            publication = self.store.publication(publication_id)
            if app.operations_store:
                if old_publication and not switching:
                    app.operations_store.adopt_publication(old_publication, publication)
                else:
                    app.operations_store.start_meet(publication)
            self.store.activate(publication_id, preserve_active_day=bool(old_publication and not switching))
            app.engine.adopt_config(publication.session_config())
            app.identities.reconcile_panels(set(app.engine.config.panels))
        else:
            if us_session and not switching:
                if us_session["status"] == "closed":
                    # Keep the completed session as history, but the next run
                    # must start from the newly selected Cloud publication.
                    app.us_store.deactivate()
                else:
                    app.us_store.adopt_package(package)
            self.store.deactivate()
            app.engine.adopt_config(unconfigured_session())
            app.identities.reconcile_panels(set())
        app.pairing.replace_valid_panels(set(app.engine.config.panels))
        app.refresh_connection_grants(new_meet=switching)
        app.lifecycle.complete_transition(ticket)
        app.refresh_clock_source()
        app.lifecycle_error = ""
        if app.on_config_applied:
            try:
                app.on_config_applied()
            except Exception:
                # Clients also refresh on presence; a transient broker outage
                # must not undo an already committed local configuration.
                pass
        self.store._save_setting("cloud_pending_id", "")
        self.store._save_setting("cloud_pending_region", "")
        self.store.clear_pending()
        self.state, self.message = "current", f"Config för {name} är uppdaterad. Pågående drift har bevarats."
        return {"pending": False, "activated": True, "publication_id": publication_id,
                "operating_region": region, "message": self.message, "restart_required": False}
