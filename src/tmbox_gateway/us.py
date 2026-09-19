"""Isolated, server-authoritative US model-railroad operations.

This is the explicit Train Meet test profile, not GCOR/ATSF/SP certification.
No EU TrafficEngine transitions, panel IDs, or Cloud calls belong here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .us_clock import clock_settings, clock_status
from .lifecycle import us_meet_id

PROFILE = "tm-us-twc-manual-v1"
SCHEMA = "trainmeet.us.runtime/1"
HOLDING = {"active", "release_requested"}
TERMINAL = {"closed", "void"}


class USError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def text(value: Any, field: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise USError(f"{field}: enter 1–{limit} characters")
    return value.strip()


def number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise USError(f"{field}: a finite number is required")
    return float(value)


def rows(value: Any, field: str, maximum: int = 1000) -> list[dict]:
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, dict) for v in value):
        raise USError(f"{field}: expected a list of at most {maximum} objects")
    return value


def indexed(value: Any, field: str) -> dict[str, dict]:
    result = {}
    for row in rows(value, field):
        key = text(row.get("id"), f"{field}.id", 80)
        if key in result:
            raise USError(f"Duplicate {field} ID: {key}")
        result[key] = row
    return result


def validate_package(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("profile") != PROFILE:
        raise USError(f"Expected {SCHEMA} with profile {PROFILE}")
    package = copy.deepcopy(value)
    text(package.get("publication_id"), "publication_id", 100)
    text(package.get("name"), "name")
    territories = indexed(package.get("territories"), "territories")
    nodes = indexed(package.get("nodes"), "nodes")
    segments = indexed(package.get("segments"), "segments")
    runs = indexed(package.get("runs"), "runs")
    if not territories or not nodes or not segments:
        raise USError("The package needs territories, named points and track segments")
    for territory in territories.values():
        text(territory.get("name"), "territory.name")
    for node in nodes.values():
        if node.get("territory_id") not in territories:
            raise USError("Unknown milepost territory")
        text(node.get("name"), "node.name")
        number(node.get("mp"), "node.mp")
        if not 0 <= number(node.get("x"), "node.x") <= 100 or not 0 <= number(node.get("y"), "node.y") <= 10000:
            raise USError("Diagram x must be 0–100 and y 0–10000")
    for segment in segments.values():
        text(segment.get("name"), "segment.name")
        a, b = nodes.get(segment.get("from_node")), nodes.get(segment.get("to_node"))
        if not a or not b or a["id"] == b["id"] or a["territory_id"] != b["territory_id"] or a["mp"] == b["mp"]:
            raise USError("Each segment needs two distinct limits in the same MP territory")
        resources = segment.get("conflict_resources", [])
        if not isinstance(resources, list) or len(resources) > 100:
            raise USError("Invalid conflict resources")
        for resource in resources:
            text(resource, "conflict_resource", 80)
    for run in runs.values():
        validate_run(run, nodes)
    try:
        clock_settings(package.get('session', {}))
    except ValueError as error:
        raise USError(str(error)) from error
    planning = package.get('planning', {})
    if not isinstance(planning, dict) or planning.get('runtime_enforced', False) is not False:
        raise USError('Dispatcher districts are planning references, not enforced permissions in this profile')
    for district in rows(planning.get('dispatcher_districts', []), 'dispatcher districts'):
        text(district.get('name'), 'dispatcher district name')
        if district.get('instructions'):
            text(district['instructions'], 'dispatcher instructions', 4000)
    for note in rows(planning.get('source_instructions', []), 'source instructions'):
        text(note.get('text'), 'source instruction', 4000)
    # Reject NaN even in extension fields before persisting anything.
    try:
        json.dumps(package, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise USError("Package must be finite JSON data") from error
    return package


def validate_run(run: dict, nodes: dict) -> None:
    text(run.get("symbol"), "train symbol", 60)
    for field in ('railroad', 'service'):
        if field in run and (not isinstance(run[field], str) or len(run[field]) > 160):
            raise USError(f'{field}: expected text up to 160 characters')
    if run.get("direction") not in {"east", "west"}:
        raise USError("Direction must be east or west")
    for stop in rows(run.get("schedule", []), "schedule", 200):
        if stop.get("node_id") not in nodes:
            raise USError("Unknown scheduled location")
        value = text(stop.get("time"), "scheduled time", 5)
        if len(value) != 5 or value[2] != ":" or not value.replace(":", "").isdigit() or int(value[:2]) > 23 or int(value[3:]) > 59:
            raise USError("Scheduled time must be HH:MM")
        if stop.get("work"):
            text(stop["work"], "scheduled work", 400)
        if stop.get('event') is not None and stop['event'] not in {'arrive', 'depart', 'pass', 'switch'}:
            raise USError('Unknown schedule event')
        if type(stop.get('day_offset', 0)) is not int or stop.get('day_offset', 0) != 0:
            raise USError('This US profile supports one timetable day per session')


def validate_path(package: dict, value: Any) -> list[dict]:
    path = copy.deepcopy(rows(value, "path", 100))
    if not path:
        raise USError("Choose at least one track segment")
    segments = indexed(package["segments"], "segments")
    nodes = indexed(package["nodes"], "nodes")
    previous_exit = None
    seen = set()
    for i, leg in enumerate(path):
        segment = segments.get(leg.get("segment_id"))
        if not segment or segment["id"] in seen:
            raise USError("Unknown or repeated track segment")
        seen.add(segment["id"])
        a, b = nodes[segment["from_node"]], nodes[segment["to_node"]]
        start, end = number(leg.get("from_mp"), "from MP"), number(leg.get("to_mp"), "to MP")
        if start == end or not min(a["mp"], b["mp"]) <= min(start, end) < max(start, end) <= max(a["mp"], b["mp"]):
            raise USError(f"Limits are outside {segment['name']} or have zero length")
        entry = a["id"] if start == a["mp"] else b["id"] if start == b["mp"] else None
        exit_node = a["id"] if end == a["mp"] else b["id"] if end == b["mp"] else None
        if i and (previous_exit is None or entry != previous_exit):
            raise USError("Track path is not connected at the specified limits")
        previous_exit = exit_node
    return path


def paths_conflict(package: dict, first: list[dict], second: list[dict]) -> bool:
    segments = indexed(package["segments"], "segments")
    for a in first:
        for b in second:
            if a["segment_id"] == b["segment_id"] and max(min(a["from_mp"], a["to_mp"]), min(b["from_mp"], b["to_mp"])) <= min(max(a["from_mp"], a["to_mp"]), max(b["from_mp"], b["to_mp"])):
                return True
            if set(segments[a["segment_id"]].get("conflict_resources", [])) & set(segments[b["segment_id"]].get("conflict_resources", [])):
                return True
    return False


def warrant_text(package: dict, warrant: dict, run: dict) -> str:
    segments = indexed(package["segments"], "segments")
    nodes = indexed(package["nodes"], "nodes")
    territories = indexed(package["territories"], "territories")
    # Generated once for a new draft, then stored verbatim. Never regenerate
    # existing warrants during a UI language change or lifecycle transition.
    direction = "EASTBOUND" if run["direction"] == "east" else "WESTBOUND"
    railroad = run.get('railroad', '').strip()
    symbol = run['symbol']
    train = f'{railroad} {symbol}' if railroad and not symbol.lower().startswith(railroad.lower() + ' ') else symbol
    if run.get('service', '').strip():
        train += ' · ' + run['service'].strip()
    lines = [f"Track Warrant {warrant['number']} · {train} · Train direction: {direction}"]
    for leg in warrant["path"]:
        segment = segments[leg["segment_id"]]
        territory = territories[nodes[segment["from_node"]]["territory_id"]]["name"]
        limits = (f"Proceed from MP {leg['from_mp']:g} to MP {leg['to_mp']:g}"
                  if warrant["kind"] == "proceed" else
                  f"Work between MP {leg['from_mp']:g} and MP {leg['to_mp']:g} (either direction)")
        lines.append(f"{territory} · {segment['name']} · {limits}")
    if warrant.get("notes"):
        lines.append(f"Additional information (not machine-validated): {warrant['notes']}")
    return "\n".join(lines)


class USStore:
    def __init__(self, path: str | Path):
        self.external_clock_source = None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS us_sessions (
            id TEXT PRIMARY KEY, state_json TEXT NOT NULL, revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS us_current (singleton INTEGER PRIMARY KEY CHECK(singleton=1), session_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS us_commands (
            actor TEXT NOT NULL, command_id TEXT NOT NULL, request_hash TEXT NOT NULL,
            result_json TEXT NOT NULL, PRIMARY KEY(actor, command_id));
          CREATE TABLE IF NOT EXISTS us_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            revision INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
            target_id TEXT, meet_time TEXT NOT NULL, recorded_at TEXT NOT NULL,
            detail_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS us_packages (
            publication_id TEXT PRIMARY KEY, package_json TEXT NOT NULL,
            checksum TEXT NOT NULL, downloaded_at TEXT NOT NULL, source_url TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS us_cloud_link (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), url TEXT NOT NULL, token TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def cloud_link(self):
        with self.lock:
            row = self.db.execute('SELECT url,token FROM us_cloud_link WHERE singleton=1').fetchone()
            return tuple(row) if row else None

    def package_catalogue(self):
        with self.lock:
            return [self._package_summary(row) for row in self.db.execute('SELECT * FROM us_packages ORDER BY downloaded_at DESC,publication_id')]

    def saved_package(self, publication_id):
        with self.lock:
            row = self.db.execute('SELECT package_json FROM us_packages WHERE publication_id=?', (text(publication_id, 'publication ID'),)).fetchone()
            if row is None:
                raise USError('Saved US package not found', 404)
            return json.loads(row[0])

    @staticmethod
    def _package_summary(row):
        package = json.loads(row['package_json'])
        return {'publication_id': row['publication_id'], 'name': package['name'],
                'checksum': row['checksum'], 'downloaded_at': row['downloaded_at'],
                'published_at': package.get('published_at', ''), 'source_url': row['source_url'],
                'counts': {key: len(package[key]) for key in ('territories', 'nodes', 'segments', 'runs')},
                'session': package.get('session', {}), 'planning': package.get('planning', {})}

    def stage_package(self, value, *, url='', token=None, expected_link=None):
        package = validate_package(value)
        encoded = json.dumps(package, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > 2_000_000:
            raise USError('US package is too large')
        checksum = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if expected_link is not None and self.cloud_link() != expected_link:
                    raise USError('Cloud connection changed. Review and download again.', 409)
                row = self.db.execute('SELECT * FROM us_packages WHERE publication_id=?', (package['publication_id'],)).fetchone()
                if row and row['checksum'] != checksum:
                    raise USError('A different package already uses this publication ID', 409)
                if not row:
                    self.db.execute('INSERT INTO us_packages VALUES(?,?,?,?,?)',
                                    (package['publication_id'], encoded, checksum, datetime.now(timezone.utc).isoformat(), url))
                if token:
                    self.db.execute('INSERT INTO us_cloud_link VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET url=excluded.url,token=excluded.token', (url, token))
                elif url and expected_link is None:
                    # A one-off download from another Config server must not
                    # silently leave the previous server selected for updates.
                    self.db.execute('DELETE FROM us_cloud_link')
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        return next(item for item in self.package_catalogue() if item['publication_id'] == package['publication_id'])

    def _load(self, session_id: str) -> dict:
        row = self.db.execute("SELECT state_json FROM us_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise USError("US session not found", 404)
        return json.loads(row[0])

    def current_session(self) -> dict | None:
        """Unfiltered internal state for the shared lifecycle, never a public API."""
        with self.lock:
            row = self.db.execute("SELECT session_id FROM us_current WHERE singleton=1").fetchone()
            return self._load(row[0]) if row else None

    def deactivate(self) -> None:
        """Archive the current pointer after an explicit, guarded meet switch."""
        with self.lock:
            self.db.execute("DELETE FROM us_current WHERE singleton=1")

    @staticmethod
    def _update_blockers(state: dict | None, package: dict) -> list[str]:
        if state is None:
            return ["No US operating session is selected."]
        previous = state["package"]
        if us_meet_id(previous) != us_meet_id(package):
            return ["A different meet requires an explicit confirmed meet switch."]
        reasons = []
        # Diagram placement is presentation, unlike MP values, endpoints, names
        # and conflict resources used to interpret an issued track warrant.
        def infrastructure(value):
            return {key: sorted(({k: v for k, v in row.items() if key != "nodes" or k not in {"x", "y"}}
                                for row in value[key]), key=lambda row: row["id"])
                    for key in ("territories", "nodes", "segments")}
        changed_infrastructure = infrastructure(previous) != infrastructure(package)
        outstanding = [w for w in state["warrants"] if w["status"] not in TERMINAL]
        if changed_infrastructure and outstanding:
            reasons.append("Waiting for outstanding track warrants to be closed or voided before changing infrastructure.")
        if changed_infrastructure and any(r.get("position") for r in state["runs"]):
            reasons.append("Infrastructure changes affect reported train positions; start a new operating session after traffic is complete.")
        old_plans = indexed(previous["runs"], "runs")
        new_plans = indexed(package["runs"], "runs")
        for run in state["runs"]:
            planned_id = run.get("planned_id")
            if not planned_id:
                continue  # Extra trains are runtime data, not Cloud config.
            plan = new_plans.get(planned_id)
            has_records = (run.get("conductor_id") or run.get("position") or run.get("ready")
                           or any(w["run_id"] == run["id"] for w in state["warrants"])
                           or any(r["run_id"] == run["id"] for r in state["reports"]))
            if plan is None and has_records:
                reasons.append(f"Train {run['symbol']} has operating records or an assigned conductor and cannot be removed automatically.")
                continue
            old_plan = old_plans.get(planned_id, {})
            if plan and plan != old_plan and any(w["run_id"] == run["id"] for w in outstanding):
                reasons.append(f"Waiting for outstanding track warrants for {run['symbol']} before changing its plan.")
            identity_fields = ("symbol", "railroad", "direction")
            if plan and has_records and any(plan.get(key) != old_plan.get(key) for key in identity_fields):
                reasons.append(f"Train {run['symbol']} has operating records; its identity and direction must remain unchanged.")
        return list(dict.fromkeys(reasons))

    def config_update_blockers(self, value: dict) -> list[str]:
        package = validate_package(value)
        with self.lock:
            return self._update_blockers(self.current_session(), package)

    def adopt_package(self, value: dict, *, expected_revision: int | None = None,
                      actor: str = "cloud-sync") -> dict:
        """Adopt config into the same session; retain warrants, run IDs and clock.

        All checks and the update share one SQLite write transaction. Parent must
        also hold the cross-engine lifecycle lock and transition marker. Candidate
        publication is saved separately before calling; a download alone does not
        invoke this method or change any operating state.
        """
        package = validate_package(value)
        encoded = json.dumps(package, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        checksum = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                state = self.current_session()
                blockers = self._update_blockers(state, package)
                if blockers:
                    raise USError(" ".join(blockers), 409)
                if expected_revision is not None and state["revision"] != expected_revision:
                    raise USError("The session changed. Check config updates again.", 409)
                if state["package"]["publication_id"] == package["publication_id"]:
                    if state["package_checksum"] != checksum:
                        raise USError("A different package already uses this publication ID", 409)
                    self.db.execute("COMMIT")
                    return {"session_id": state["id"], "revision": state["revision"], "publication_id": package["publication_id"], "changed": False}
                saved = self.db.execute("SELECT checksum FROM us_packages WHERE publication_id=?", (package["publication_id"],)).fetchone()
                if not saved or saved[0] != checksum:
                    raise USError("Download and validate the config before activation.", 409)
                by_plan = {r["planned_id"]: r for r in state["runs"] if r.get("planned_id")}
                runtime_fields = ("id", "planned_id", "conductor_id", "conductor_name", "position", "ready")
                runs = []
                for plan in package["runs"]:
                    old = by_plan.get(plan["id"])
                    runtime = ({key: old.get(key) for key in runtime_fields} if old else
                               {"id": str(uuid4()), "planned_id": plan["id"], "conductor_id": None,
                                "conductor_name": None, "position": None, "ready": False})
                    runs.append({**plan, **runtime})
                runs.extend(r for r in state["runs"] if not r.get("planned_id"))
                old_publication = state["package"]["publication_id"]
                state.update(package=package, package_checksum=checksum, name=package["name"],
                             runs=runs, revision=state["revision"] + 1)
                self.db.execute("UPDATE us_sessions SET state_json=?,revision=? WHERE id=?",
                                (json.dumps(state, allow_nan=False), state["revision"], state["id"]))
                self.db.execute("INSERT INTO us_events(session_id,revision,actor,action,target_id,meet_time,recorded_at,detail_json) VALUES(?,?,?,?,?,?,?,?)",
                                (state["id"], state["revision"], actor, "config_updated", package["publication_id"],
                                 self.meeting_clock(state["clock"])["time"] if state.get("clock") else "",
                                 datetime.now(timezone.utc).isoformat(), json.dumps({"previous_publication_id": old_publication, "publication_id": package["publication_id"]})))
                self.db.execute("COMMIT")
                return {"session_id": state["id"], "revision": state["revision"], "publication_id": package["publication_id"], "changed": True}
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def context(self, actor: str, dispatcher: bool) -> dict:
        with self.lock:
            self.db.execute("BEGIN")
            try:
                row = self.db.execute("SELECT session_id FROM us_current WHERE singleton=1").fetchone()
                state = self._load(row[0]) if row else None
                if state:
                    visible = {r["id"] for r in state["runs"] if dispatcher or r.get("conductor_id") == actor}
                    state["runs"] = [r for r in state["runs"] if r["id"] in visible]
                    state["warrants"] = [w for w in state["warrants"] if w["run_id"] in visible]
                    state["reports"] = [r for r in state["reports"] if r["run_id"] in visible]
                    state["events"] = [dict(e) for e in self.db.execute(
                        "SELECT sequence,revision,actor,action,target_id,meet_time,recorded_at FROM us_events WHERE session_id=? ORDER BY sequence DESC LIMIT 200", (state["id"],))
                        if dispatcher or e["actor"] == actor or e["target_id"] in visible or e["target_id"] in {w["id"] for w in state["warrants"]}]
                    # Conductor receives infrastructure and only its own plan.
                    if not dispatcher:
                        state["package"]["runs"] = []
                self.db.execute("COMMIT")
                result = {"actor": actor, "role": "dispatcher" if dispatcher else "conductor", "session": state}
                if state and state.get('clock'):
                    result['clock'] = self.meeting_clock(state['clock'])
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def command_status(self, actor: str, command_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT result_json FROM us_commands WHERE actor=? AND command_id=?", (actor, command_id)).fetchone()
            return json.loads(row[0]) if row else None

    def execute(self, actor: str, dispatcher: bool, action: str, payload: dict, meet_time: str) -> dict:
        command_id = text(payload.get("command_id"), "command_id", 100)
        try:
            encoded = json.dumps({"action": action, "payload": payload}, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise USError("Invalid JSON command") from error
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT request_hash,result_json FROM us_commands WHERE actor=? AND command_id=?", (actor, command_id)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise USError("Command ID already used for different data", 409)
                    self.db.execute("COMMIT")
                    return json.loads(old[1])
                if action == "create_session":
                    if not dispatcher:
                        raise USError("Dispatcher access required", 403)
                    current = self.db.execute("SELECT session_id FROM us_current WHERE singleton=1").fetchone()
                    if current and self._load(current[0])["status"] != "closed":
                        raise USError("Finish the current session before activating a new package", 409)
                    if payload.get('publication_id'):
                        if payload.get('confirmed') is not True:
                            raise USError('Review the package and confirm before starting')
                        row = self.db.execute('SELECT * FROM us_packages WHERE publication_id=?', (payload['publication_id'],)).fetchone()
                        if not row or row['checksum'] != payload.get('package_checksum'):
                            raise USError('The selected package is unavailable or changed. Review it again.', 409)
                        package = validate_package(json.loads(row['package_json']))
                    else:
                        package = validate_package(payload.get("package"))
                    state = {"id": str(uuid4()), "name": package["name"], "package": package,
                             "package_checksum": hashlib.sha256(json.dumps(package, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
                             "status": "running", "revision": 0, "runs": [], "warrants": [], "reports": [],
                             "clock": clock_settings(package.get('session', {}))}
                    meet_time = self.meeting_clock(state['clock'])['time']
                    for plan in package["runs"]:
                        state["runs"].append({**plan, "planned_id": plan["id"], "id": str(uuid4()), "conductor_id": None, "conductor_name": None, "position": None, "ready": False})
                    target = state["id"]
                else:
                    session_id = text(payload.get("session_id"), "session_id", 100)
                    current = self.db.execute("SELECT session_id FROM us_current WHERE singleton=1").fetchone()
                    if not current or current[0] != session_id:
                        raise USError("This command belongs to a previous operating session", 409)
                    state = self._load(session_id)
                    if state["status"] != "running":
                        raise USError("Session is closed", 409)
                    if type(payload.get("expected_revision")) is not int or payload["expected_revision"] != state["revision"]:
                        raise USError("The session changed. Refresh and review before trying again.", 409)
                    if state.get('clock'):
                        meet_time = self.meeting_clock(state['clock'])['time']
                    target = self._apply(state, actor, dispatcher, action, payload, now, meet_time)
                state["revision"] += 1
                result = {"command_id": command_id, "session_id": state["id"], "revision": state["revision"], "target_id": target}
                self.db.execute("INSERT INTO us_sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json,revision=excluded.revision", (state["id"], json.dumps(state, allow_nan=False), state["revision"]))
                if action == "create_session":
                    self.db.execute("INSERT INTO us_current VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET session_id=excluded.session_id", (state["id"],))
                self.db.execute("INSERT INTO us_events(session_id,revision,actor,action,target_id,meet_time,recorded_at,detail_json) VALUES(?,?,?,?,?,?,?,?)", (state["id"], state["revision"], actor, action, target, meet_time, now, encoded))
                self.db.execute("INSERT INTO us_commands VALUES(?,?,?,?)", (actor, command_id, fingerprint, json.dumps(result)))
                self.db.execute("COMMIT")
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def meeting_clock(self, clock):
        external = self.external_clock_source() if self.external_clock_source else None
        return external if external is not None else clock_status(clock)

    def _apply(self, state: dict, actor: str, dispatcher: bool, action: str, payload: dict, now: str, meet_time: str) -> str:
        conductor_actions = {"receive", "readback", "request_release", "report", "ready"}
        dispatcher_actions = {"draft", "transmit", "activate", "close_warrant", "void", "assign", "extra", "finish_session", "clock"}
        if action not in conductor_actions | dispatcher_actions:
            raise USError("Unknown US action")
        if (action in dispatcher_actions) != dispatcher:
            raise USError("This action belongs to the other operating role", 403)
        if action == 'clock':
            if self.external_clock_source and self.external_clock_source() is not None:
                raise USError('FastClock controls the time and speed. Use the server clock controls to start or stop.', 409)
            if payload.get('confirmed') is not True or type(payload.get('running')) is not bool:
                raise USError('Confirm the US clock change')
            try:
                new_clock = clock_settings(payload)
            except ValueError as error:
                raise USError(str(error)) from error
            new_clock['running'] = payload['running']
            state['clock'] = new_clock
            return state['id']
        if action == "finish_session":
            if any(w["status"] not in TERMINAL for w in state["warrants"]):
                raise USError("Close or void all warrants first", 409)
            state["status"] = "closed"
            if state.get('clock'):
                state['clock'].update(seconds=clock_status(state['clock'])['seconds'], running=False, anchor=time.time())
            return state["id"]
        if action == "extra":
            run = {"id": str(uuid4()), "symbol": payload.get("symbol"), "direction": payload.get("direction"), "locomotive": "", "schedule": [], "planned_id": None, "conductor_id": None, "conductor_name": None, "position": None, "ready": False}
            validate_run(run, indexed(state["package"]["nodes"], "nodes"))
            state["runs"].append(run)
            return run["id"]
        warrant = next((w for w in state["warrants"] if w["id"] == payload.get("warrant_id")), None)
        run_id = warrant["run_id"] if warrant else payload.get("run_id")
        run = next((r for r in state["runs"] if r["id"] == run_id), None)
        if not run:
            raise USError("Train run not found", 404)
        if not dispatcher and run.get("conductor_id") != actor:
            raise USError("This train run is not assigned to you", 403)
        if action == "assign":
            if any(w["run_id"] == run_id and w["status"] not in TERMINAL | {"draft"} for w in state["warrants"]):
                raise USError("Finish outstanding warrants before changing conductor", 409)
            run["conductor_id"] = text(payload.get("conductor_id"), "conductor_id", 100)
            run["conductor_name"] = text(payload.get("conductor_name"), "conductor name", 80)
            run["ready"] = False
        elif action == "ready":
            run["ready"] = True
        elif action == "report":
            kind = payload.get("kind")
            if kind not in {"position", "request", "delay", "problem"}:
                raise USError("Unknown report type")
            report = {"id": str(uuid4()), "run_id": run_id, "kind": kind, "message": text(payload.get("message"), "report", 1000), "actor": actor, "recorded_at": now, "meet_time": meet_time}
            if kind == "position":
                position = payload.get("position")
                if not isinstance(position, dict):
                    raise USError("Position must identify a segment and MP")
                segment = next((s for s in state["package"]["segments"] if s["id"] == position.get("segment_id")), None)
                if not segment:
                    raise USError("Unknown position segment")
                nodes = indexed(state["package"]["nodes"], "nodes")
                limits = [nodes[segment[k]]["mp"] for k in ("from_node", "to_node")]
                mp = number(position.get("mp"), "position MP")
                if not min(limits) <= mp <= max(limits):
                    raise USError("Position is outside the segment")
                report["position"] = {"segment_id": segment["id"], "mp": mp, "certainty": "reported", "recorded_at": now, "meet_time": meet_time}
                run["position"] = report["position"]
            state["reports"].append(report)
        elif action == "draft":
            if payload.get("kind") not in {"proceed", "work"}:
                raise USError("Choose Proceed or Work")
            path = validate_path(state["package"], payload.get("path"))
            notes = payload.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 1000:
                raise USError("Additional information is limited to 1000 characters")
            warrant = {"id": str(uuid4()), "number": f"W{len(state['warrants']) + 1:03}", "run_id": run_id, "kind": payload["kind"], "path": path, "notes": notes, "status": "draft", "times": {"draft": {"actor": actor, "recorded_at": now, "meet_time": meet_time}}}
            warrant["text"] = warrant_text(state["package"], warrant, run)
            state["warrants"].append(warrant)
            return warrant["id"]
        else:
            if not warrant:
                raise USError("Warrant not found", 404)
            transitions = {"transmit": ("draft", "transmitted"), "receive": ("transmitted", "received"),
                           "readback": ("received", "readback_pending"), "activate": ("readback_pending", "active"),
                           "request_release": ("active", "release_requested"), "close_warrant": ("release_requested", "closed")}
            if action == "void":
                if warrant["status"] not in {"draft", "transmitted", "received", "readback_pending"}:
                    raise USError("Active authority needs an explicit release and closure", 409)
                status = "void"
            else:
                expected, status = transitions[action]
                if warrant["status"] != expected:
                    raise USError(f"Expected {expected}, found {warrant['status']}", 409)
            if action == "transmit" and not run.get("conductor_id"):
                raise USError("Assign a conductor before transmitting", 409)
            if action in {"readback", "activate", "request_release", "close_warrant", "void"} and payload.get("confirmed") is not True:
                raise USError("Explicit confirmation is required")
            if action == "activate":
                for other in state["warrants"]:
                    if other["id"] != warrant["id"] and other["status"] in HOLDING and paths_conflict(state["package"], warrant["path"], other["path"]):
                        raise USError(f"Conflict with {other['number']}; no authority activated", 409)
            warrant["status"] = status
            warrant["times"][status] = {"actor": actor, "recorded_at": now, "meet_time": meet_time}
            return warrant["id"]
        return run["id"]
