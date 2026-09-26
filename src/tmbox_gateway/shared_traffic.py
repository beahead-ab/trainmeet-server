"""Server-side v1 keypad adapter. SQLite station operations own all traffic.

Only the 16x2 screen and input session live in TrafficEngine. Connections here
are disposable read projections, never a second source of clearance decisions.
"""
from __future__ import annotations

from dataclasses import asdict

from .models import ConnectionRuntime, ConnectionState, RequestStatus
from .protocol_v2 import CommandRejected, TMBoxStationService


class SharedPanelTraffic:
    def __init__(self, engine, service: TMBoxStationService):
        self.engine = engine
        self.service = service
        self._fingerprint = None
        self._views = {}
        # Never silently discard an active legacy run during an upgrade.
        # Upgrade at a free-line boundary until an explicit migration exists.
        if not engine.shared_state_restored and any(line.state != ConnectionState.FREE for line in engine.connections.values()):
            raise RuntimeError(
                "Aktiva äldre tågklareringar finns. Avsluta trafikärendena i den "
                "tidigare serverversionen innan gemensam trafiklogik aktiveras. "
                "Ingen trafikdata har raderats."
            )
        engine._lock = service.operations_store.command_lock
        engine.shared_traffic = self
        engine.set_transition_observer(None)  # positions belong to the shared transaction
        self.refresh()

    def cases(self):
        return self.service.open_cases(None)

    def line(self, case):
        return ConnectionRuntime(
            state=(ConnectionState.REQUESTED if case["status"] == "waiting" else
                   ConnectionState.OCCUPIED if self.service.case_departed(case) else ConnectionState.RESERVED),
            from_station_id=case["from_station_id"], to_station_id=case["to_station_id"],
            train_number=self.service.case_train_number(case), request_id=case["clearance_id"],
            request_status=RequestStatus.PENDING if case["status"] == "waiting" else RequestStatus.ACCEPTED,
        )

    def view(self, station_id=None, *, panel=True):
        result = {key: ConnectionRuntime() for key in self.engine.config.connections}
        cases = self.cases()
        # A 16x2 panel has one slot per connection. On double track, show the
        # incoming action first; after answering it the outgoing case is shown.
        def priority(case):
            incoming = case["to_station_id"] == station_id
            return (0 if incoming and (case["status"] == "waiting" or self.service.case_departed(case))
                    else 1 if case["from_station_id"] == station_id else 2)
        for case in sorted(cases, key=priority, reverse=True):
            if case["connection_id"] in result:
                result[case["connection_id"]] = self.line(case)
        if station_id and panel:
            for key, line in list(result.items()):
                connection = self.engine.config.connections[key]
                if (connection.track_type.value == "double" and line.to_station_id == station_id
                        and line.state == ConnectionState.RESERVED):
                    # The opposite track is still free for a new departure.
                    result[key] = ConnectionRuntime()
        return result

    def connection_states(self, station_id=None):
        """Full HTTP view retains both directed channels, unlike a 16x2 slot."""
        with self.service.operations_store.command_lock:
            self.refresh()
            cases = self.cases()
            return [{"id": key, **asdict(line), "channels": [
                {"channel_id": case["channel_id"], **asdict(self.line(case))}
                for case in cases if case["connection_id"] == key
            ]} for key, line in self.view(station_id, panel=False).items()]

    def refresh(self):
        with self.service.operations_store.command_lock:
            cases = self.cases()
            publication = self.service.publication()
            fingerprint = (publication.publication_id if publication else None,
                           self.service.runtime_scope().get("meet_generation"),
                           self.service.runtime_store.active_day(),
                           tuple(sorted((c["clearance_id"], c["revision"], self.service.case_departed(c)) for c in cases)))
            if fingerprint == self._fingerprint:
                self.engine.connections = self.view()
                return
            # Drop obsolete confirmation screens, never replay an action for a
            # new train which happens to use the same slot.
            for panel_id, panel in self.engine.config.panels.items():
                runtime = self.engine.panels[panel_id]
                view = self.view(panel.station_id)
                previous = self._views.get(panel_id, {})
                connection_id = panel.slots.get(runtime.selected_slot)
                if runtime.selected_slot and previous.get(connection_id) != view.get(connection_id):
                    runtime.reset()
                self._views[panel_id] = view
            self.engine.connections = self.view()
            self.engine.revision += 1
            self._fingerprint = fingerprint

    def perform(self, actor, station_id, connection_id, action, train_number=""):
        with self.service.operations_store.command_lock:
            try:
                config = self.service.session_config()
                connection = config.connections.get(connection_id) if config else None
                if connection is None or station_id not in (connection.station_a_id, connection.station_b_id):
                    raise CommandRejected("unknown_connection")
                if action == "request":
                    movement = self.service.resolve_number(station_id, train_number)
                    operation = "clearance.request"
                    body = {"movement_id": movement["id"], "connection_id": connection_id}
                else:
                    candidates = [case for case in self.service.open_cases(station_id)
                                  if case["connection_id"] == connection_id
                                  and case["to_station_id" if action in {"accept", "reject", "arrive"}
                                           else "from_station_id"] == station_id]
                    if len(candidates) != 1:
                        raise CommandRejected("request_no_longer_pending")
                    case = candidates[0]
                    if action in {"accept", "reject", "cancel"}:
                        operation = "clearance.cancel" if action == "cancel" else "clearance.response"
                        body = {"clearance_id": case["clearance_id"], "approved": action == "accept"}
                    elif action == "depart":
                        operation, body = "train.departed", {"movement_id": case["movement_id"]}
                    elif action == "arrive":
                        if not self.service.case_departed(case):
                            raise CommandRejected("train_not_departed")
                        movement = self.service.resolve_number(station_id, self.service.case_train_number(case), arrival=True)
                        operation, body = "train.arrived", {"movement_id": movement["id"]}
                    else:
                        raise CommandRejected("unknown_action")
                self.service.execute_station_command(actor, station_id, operation, body)
                self.refresh()
                return True, None
            except CommandRejected as error:
                return False, error.reason
