"""Private host Wi-Fi control. Never part of meet settings or backups.

The HTTP process is an unprivileged client of a fixed local socket. The root
helper owns the allowlisted operations and NetworkManager owns all secrets.
"""
from __future__ import annotations

import json
import secrets
import socket
import time
from uuid import UUID

SOCKET_PATH = "/run/trainmeet-network/control.sock"
MAX_BYTES = 65536
TRIAL_SECONDS = 90
SECURITY = {"open", "wpa2", "wpa3"}


class NetworkError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise NetworkError(code, message)


def uuid_value(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        fail("invalid_profile", "Ogiltig nätverksprofil.")
    return value


def validate_profile(data, existing=None):
    if not isinstance(data, dict) or set(data) - {"uuid", "ssid", "security", "password", "hidden"}:
        fail("invalid_profile", "Ogiltig nätverksprofil.")
    ssid = data.get("ssid")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        fail("invalid_ssid", "SSID ska vara 1–32 byte och får inte innehålla kontrolltecken.")
    security = data.get("security")
    if not isinstance(security, str) or security not in SECURITY or type(data.get("hidden", False)) is not bool:
        fail("invalid_security", "Välj WPA2, WPA3 eller öppet nätverk.")
    password = data.get("password", "")
    if not isinstance(password, str) or any(ord(c) < 32 or ord(c) == 127 for c in password):
        fail("invalid_password", "Ogiltigt Wi-Fi-lösenord.")
    keep = not password and existing and existing["security"] == security and existing["ssid"] == ssid
    if security != "open" and not keep:
        # Personal passphrases, not raw 64-digit PSKs. Same UI rule for SAE.
        if not 8 <= len(password.encode("utf-8")) <= 63:
            fail("invalid_password", "Wi-Fi-lösenordet ska vara 8–63 byte.")
    if security == "open" and password:
        fail("invalid_password", "Öppet nätverk ska inte ha lösenord.")
    return {"ssid": ssid, "security": security, "hidden": data.get("hidden", False), "password": password}


def host_request(action="status", payload=None, actor=""):
    """No credentials in filenames, argv, logs, normal DB, or error messages."""
    request = json.dumps({"action": action, "payload": payload or {}, "actor": actor}).encode() + b"\n"
    if len(request) > MAX_BYTES:
        fail("invalid_request", "Nätverksbegäran är för stor.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(12)
            client.connect(SOCKET_PATH)
            client.sendall(request)
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
    except (OSError, ValueError):
        if action == "status":
            return {"supported": False, "reason": "Nätverkshantering kräver Raspberry Pi med installerad nätverkstjänst."}
        fail("host_unavailable", "Pi:ns nätverkstjänst svarar inte. Kontrollera status innan du försöker igen.")
    if "error" in result:
        fail(result["error"], result["message"])
    return result


class NetworkController:
    """Serialized by the socket server. Backend is replaceable in tests."""
    def __init__(self, backend, clock=time.monotonic):
        self.backend = backend
        self.clock = clock
        self.pending = None
        self.last_scan = {}

    def _expire(self):
        if self.pending and (self.clock() >= self.pending["deadline"] or not self.backend.checkpoint_exists(self.pending["checkpoint"])):
            # NM's timer is independent of this process. Never destroy an
            # expired checkpoint: that would cancel a still-pending rollback.
            self.pending = None

    def status(self, actor):
        self._expire()
        result = self.backend.status()
        result["supported"] = True
        result["recovering"] = not self.pending and self.backend.has_checkpoints()
        pending = self.pending
        result["trial"] = None
        if pending:
            result["trial"] = {"uuid": pending["uuid"], "interface": pending["interface"],
                               "remaining": max(0, int(pending["deadline"] - self.clock())),
                               "token": pending["token"] if pending["actor"] == actor else None}
        return result

    def handle(self, action, payload, actor):
        if not isinstance(action, str) or not isinstance(payload, dict) or not isinstance(actor, str) or not actor or len(actor) > 160:
            fail("invalid_request", "Ogiltig nätverksbegäran.")
        self._expire()
        if action == "status":
            return self.status(actor)
        if action in {"confirm", "rollback"}:
            if set(payload) != {"token"} or not self.pending or self.pending["actor"] != actor or not secrets.compare_digest(str(payload["token"]), self.pending["token"]):
                fail("invalid_trial", "Provanslutningen är avslutad eller tillhör en annan administratör.")
            pending = self.pending
            if action == "confirm":
                if not self.backend.connected(pending["interface"], pending["uuid"]):
                    fail("not_connected", "Det nya nätverket är inte anslutet ännu.")
                self.backend.commit_checkpoint(pending["checkpoint"])
            else:
                self.backend.rollback(pending["checkpoint"])
            self.pending = None
            return self.status(actor)
        if self.pending:
            # Retrying a timed-out connect must not create a second trial.
            if action == "connect" and self.pending["actor"] == actor and payload == {"uuid": self.pending["uuid"], "interface": self.pending["interface"]}:
                return self.status(actor)
            fail("trial_pending", "Bekräfta eller återställ den pågående provanslutningen först.")
        if self.backend.has_checkpoints():
            # After a helper restart NM still owns the timer. Do not cancel or
            # overwrite that checkpoint (or another administrator's checkpoint).
            fail("network_recovering", "En nätverksåterställning pågår. Vänta tills den är klar.")
        profiles = self.backend.status()["profiles"]
        if action == "scan":
            if set(payload) != {"interface"}:
                fail("invalid_request", "Välj ett trådlöst nätverkskort.")
            interface = payload["interface"]
            self.backend.device(interface)
            if self.clock() - self.last_scan.get(interface, -100) >= 10:
                self.backend.scan(interface)
                self.last_scan[interface] = self.clock()
            return self.status(actor)
        if action == "save":
            ident = payload.get("uuid")
            existing = self._profile(profiles, ident) if ident else None
            data = validate_profile(payload, existing)
            if any(p["ssid"] == data["ssid"] and p["uuid"] != ident for p in profiles):
                fail("duplicate_ssid", "Nätverket finns redan. Redigera den sparade profilen.")
            if not existing and len(profiles) >= 32:
                fail("profile_limit", "Högst 32 Wi-Fi-profiler kan hanteras här.")
            # Never rewrite a live profile's credentials: otherwise a later
            # trial checkpoint could no longer restore the previous password.
            if existing and existing["active"]:
                fail("active_profile", "Byt Wi-Fi-nätverk innan den aktiva profilen ändras.")
            self.backend.save(ident, data)
        elif action == "delete":
            if set(payload) != {"uuid"}:
                fail("invalid_profile", "Ogiltig nätverksprofil.")
            profile = self._profile(profiles, payload["uuid"])
            if profile["active"]:
                fail("active_profile", "Det aktiva nätverket kan inte tas bort.")
            self.backend.delete(profile["uuid"])
        elif action == "order":
            ids = payload.get("uuids")
            expected = {p["uuid"] for p in profiles if p["editable"]}
            if set(payload) != {"uuids"} or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != expected:
                fail("stale_profiles", "Nätverkslistan har ändrats. Läs in den igen.")
            self.backend.order(ids)
        elif action == "connect":
            if set(payload) != {"uuid", "interface"}:
                fail("invalid_request", "Välj nätverk och nätverkskort.")
            profile = self._profile(profiles, payload["uuid"])
            interface = payload["interface"]
            device = self.backend.device(interface)
            if self.backend.connected(interface, profile["uuid"]):
                return self.status(actor)
            checkpoint = self.backend.checkpoint(device, TRIAL_SECONDS)
            self.pending = {"checkpoint": checkpoint, "uuid": profile["uuid"], "interface": interface,
                            "token": secrets.token_urlsafe(24), "actor": actor, "deadline": self.clock() + TRIAL_SECONDS}
            try:
                self.backend.activate(profile["uuid"], device)
            except Exception:
                self.backend.rollback(checkpoint)
                self.pending = None
                fail("activation_failed", "Anslutningen kunde inte startas. Tidigare anslutning återställs.")
        else:
            fail("invalid_action", "Okänd nätverksåtgärd.")
        return self.status(actor)

    @staticmethod
    def _profile(profiles, ident):
        uuid_value(ident)
        profile = next((p for p in profiles if p["uuid"] == ident), None)
        if not profile or not profile["editable"]:
            fail("invalid_profile", "Denna profil kan inte hanteras här.")
        return profile
