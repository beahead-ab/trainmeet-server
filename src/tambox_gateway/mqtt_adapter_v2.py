from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .http_server import HTTPAPIError, TamboxHTTPApplication
from .identity import IdentityStore, PairedClient

LOGGER = logging.getLogger("tambox_gateway.mqtt_v2")

PROTOCOL_VERSION = 2

# train.lookup is intentionally not a wire command. Per the §3.4 decision in
# trainmeet-tambox docs/underlag/tmbox-monsterprompt-v2.md, the box holds its
# station's movements locally from the retained snapshot topic and looks
# trains up against that cache — there is nothing to send over the wire for
# a lookup.
ACTION_HANDLERS: dict[str, str] = {
    "train.position.set": "_handle_position",
    "train.crew_ready.set": "_handle_crew_ready",
    "train.track.change": "_handle_track_change",
    "train.departed": "_handle_departed",
    "train.arrived": "_handle_arrived",
    "train.approaching": "_handle_approaching",
    "clearance.request": "_handle_clearance_request",
    "clearance.response": "_handle_clearance_response",
    "clearance.cancel": "_handle_clearance_cancel",
    "line.available.publish": "_handle_line_publish",
    "line.available.acknowledge": "_handle_line_acknowledge",
}


class MQTTGatewayAdapterV2:
    """Protocol v2 MQTT transport.

    Station-scoped config + snapshot (both retained, both replaced wholesale
    on every publish — no delta logic, matching §3.4a), and complete
    idempotent commands only. This adapter is transport only: every command
    is routed straight into the already-built and tested
    TamboxHTTPApplication.v2_* methods, which is also what the web
    simulator calls over HTTP. No business logic is duplicated here.
    """

    def __init__(
        self,
        application: TamboxHTTPApplication,
        identities: IdentityStore,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        gateway_id: str = "gateway-local",
    ):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:  # pragma: no cover - exercised by deployment
            raise RuntimeError("Install the mqtt optional dependency: pip install -e '.[mqtt]'") from error

        self.mqtt = mqtt
        self.application = application
        self.identities = identities
        self.host = host
        self.port = port
        self.gateway_id = gateway_id
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"tmbox-gateway-v2-{gateway_id}",
            protocol=mqtt.MQTTv5,
            reconnect_on_failure=True,
            manual_ack=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.will_set(
            f"tmbox/v2/gateway/{gateway_id}/status",
            payload=json.dumps({"status": "offline"}),
            qos=1,
            retain=True,
        )

    def run_forever(self) -> None:
        LOGGER.info("Connecting to MQTT broker at %s:%s", self.host, self.port)
        self.client.connect(self.host, self.port, keepalive=10, clean_start=True)
        self.client.loop_forever(retry_first_connection=True)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            LOGGER.error("Broker rejected connection: %s", reason_code)
            return
        client.subscribe("tmbox/v2/device/+/hello", qos=1)
        client.subscribe("tmbox/v2/device/+/presence", qos=1)
        client.subscribe("tmbox/v2/device/+/command", qos=1)
        client.subscribe("tmbox/v2/device/+/config/ack", qos=1)
        client.publish(
            f"tmbox/v2/gateway/{self.gateway_id}/status",
            json.dumps({"status": "online"}),
            qos=1,
            retain=True,
        )
        self._publish_all_stations()
        LOGGER.info("Gateway v2 online")

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        LOGGER.warning("Gateway v2 disconnected from broker: %s", reason_code)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        del userdata
        try:
            topic_parts = message.topic.split("/")
            if len(topic_parts) < 5 or topic_parts[:3] != ["tmbox", "v2", "device"]:
                raise ValueError(f"Unexpected v2 topic: {message.topic}")
            device_id = topic_parts[3]
            rest = "/".join(topic_parts[4:])
            if rest == "hello":
                self._handle_hello(device_id, message.payload)
            elif rest == "presence":
                self._publish_device(device_id)
            elif rest == "config/ack":
                LOGGER.debug("Device %s acknowledged config", device_id)
            elif rest == "command":
                self._handle_command(device_id, message.payload)
            else:
                raise ValueError(f"Unexpected v2 message kind: {rest}")
        except Exception:  # pragma: no cover - defensive transport boundary
            LOGGER.exception("Rejected malformed v2 MQTT message on %s", message.topic)
        finally:
            client.ack(message.mid, message.qos)

    def _handle_hello(self, device_id: str, raw_payload: bytes) -> None:
        payload = json.loads(raw_payload.decode("utf-8"))
        device = self.identities.record_discovery(
            device_id,
            str(payload["device_code"]),
            model=str(payload.get("model", "TMBox")),
            firmware_version=str(payload.get("firmware_version", "unknown")),
        )
        station_id = self.identities.station_for_client(device_id)
        self.client.publish(
            f"tmbox/v2/device/{device_id}/assignment",
            json.dumps(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "status": "assigned" if station_id else "waiting_for_assignment",
                    "device_id": device_id,
                    "device_code": device.device_code,
                    "station_id": station_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            qos=1,
            retain=True,
        )
        if station_id:
            self._publish_device(device_id)

    def _publish_all_stations(self) -> None:
        for paired_client in self.identities.enabled_clients():
            if paired_client.station_id:
                self._publish_device(paired_client.client_id)

    def _publish_device(self, device_id: str) -> None:
        client = self.identities.client(device_id)
        if client is None or not client.station_id:
            return
        try:
            full = self.application.v2_station_snapshot(client, client.station_id)
        except HTTPAPIError as error:
            LOGGER.warning("Could not build v2 snapshot for %s: %s", device_id, error)
            return
        config_payload = {
            "protocol_version": PROTOCOL_VERSION,
            # publication_id doubles as the config-version marker: a new
            # driftpaket-aktivering always gets a new id, exactly the event
            # that should invalidate a box's cached tracks/topology.
            "config_version": full["publication_id"],
            "station": full["station"],
            "tracks": full["tracks"],
            "connections": full["connections"],
        }
        snapshot_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "station_id": full["station"]["id"],
            "active_day": full["active_day"],
            "movements": full["movements"],
            "active_clearances": full["active_clearances"],
            "line_messages": full["line_messages"],
            "clock": full["clock"],
        }
        self.client.publish(
            f"tmbox/v2/device/{device_id}/config",
            json.dumps(config_payload, ensure_ascii=False, separators=(",", ":")),
            qos=1,
            retain=True,
        )
        self.client.publish(
            f"tmbox/v2/device/{device_id}/snapshot",
            json.dumps(snapshot_payload, ensure_ascii=False, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    def _handle_command(self, device_id: str, raw_payload: bytes) -> None:
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            LOGGER.warning("Malformed v2 command payload from %s", device_id)
            return
        message_id = str(payload.get("message_id") or "")

        if payload.get("protocol_version") != PROTOCOL_VERSION:
            self._publish_ack(device_id, message_id, "rejected", "unsupported_protocol_version")
            return
        if str(payload.get("device_id") or "") != device_id:
            self._publish_ack(device_id, message_id, "rejected", "device_id_mismatch")
            return

        client = self.identities.client(device_id)
        station_id = str(payload.get("station_id") or "")
        if client is None or client.station_id != station_id:
            self._publish_ack(device_id, message_id, "rejected", "station_not_assigned")
            return

        action = str(payload.get("action") or "")
        handler_name = ACTION_HANDLERS.get(action)
        if handler_name is None:
            self._publish_ack(device_id, message_id, "rejected", "unknown_action")
            return
        handler: Callable[[PairedClient, str, dict[str, Any]], dict[str, Any]] = getattr(self, handler_name)

        stale = self._stale_revision(client, station_id, payload.get("expected_revision"))
        if stale is not None:
            self._publish_ack(device_id, message_id, "rejected", "stale_revision", revision=stale)
            return

        try:
            result = handler(client, station_id, payload.get("payload") or {})
        except HTTPAPIError as error:
            self._publish_ack(device_id, message_id, "rejected", error.code)
            return
        if isinstance(result, dict) and result.get("status") == "rejected":
            self._publish_ack(device_id, message_id, "rejected", result.get("reason"))
            self._publish_device(device_id)
            return

        self._publish_ack(device_id, message_id, "accepted", None, result=result)
        self._publish_all_stations()

    def _stale_revision(
        self,
        client: PairedClient,
        station_id: str,
        expected: Any,
    ) -> dict[str, Any] | None:
        """Best-effort optimistic-concurrency check (protocol v2-kontrakt §5).

        Deliberately permissive: if the claimed scope/key can't be resolved
        (movement or case not found, no revision claim supplied at all), the
        check is skipped rather than blocking the command — the store-level
        validations (track occupancy, channel busy, request no longer
        pending) remain the real safety net either way. This only catches
        the case it can resolve with confidence: a client acting on data it
        knows to be stale.
        """
        if not isinstance(expected, dict):
            return None
        scope = expected.get("scope")
        value = expected.get("value")
        current: Any = None
        if scope == "movement":
            try:
                snapshot = self.application.v2_station_snapshot(client, station_id)
                movement = next(
                    (m for m in snapshot["movements"] if m.get("id") == expected.get("movement_id")),
                    None,
                )
                current = movement["revision"] if movement else None
            except HTTPAPIError:
                return None
        elif scope == "case":
            try:
                current = self.application.operations_store.clearance(str(expected.get("case_id")))["revision"]
            except (ValueError, AttributeError):
                return None
        else:
            return None
        if current is None or current == value:
            return None
        return {"scope": scope, "value": current}

    def _publish_ack(
        self,
        device_id: str,
        message_id: str,
        status: str,
        reason: str | None,
        *,
        result: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
    ) -> None:
        ack_payload: dict[str, Any] = {"message_id": message_id, "status": status, "reason": reason}
        if result is not None:
            ack_payload["result"] = result
        if revision is not None:
            ack_payload["revision"] = revision
        self.client.publish(
            f"tmbox/v2/device/{device_id}/ack",
            json.dumps(ack_payload, ensure_ascii=False, separators=(",", ":")),
            qos=1,
            retain=False,
        )

    # -- action handlers: transport only, all logic lives in TamboxHTTPApplication --

    def _handle_position(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_movement_command(
            client,
            {
                "station_id": station_id,
                "movement_id": payload.get("movement_id"),
                "action": "position",
                "actual_track": payload.get("actual_track_id"),
            },
        )["movement"]

    def _handle_crew_ready(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_movement_command(
            client,
            {
                "station_id": station_id,
                "movement_id": payload.get("movement_id"),
                "action": "crew_ready",
                "crew_ready": bool(payload.get("crew_ready", True)),
            },
        )["movement"]

    def _handle_track_change(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_assign_track(
            client,
            {
                "station_id": station_id,
                "movement_id": payload.get("movement_id"),
                "track_id": payload.get("to_track_id") or payload.get("track_id"),
            },
        )["movement"]

    def _handle_departed(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_movement_command(
            client,
            {"station_id": station_id, "movement_id": payload.get("movement_id"), "action": "departed"},
        )["movement"]

    def _handle_arrived(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_movement_command(
            client,
            {"station_id": station_id, "movement_id": payload.get("movement_id"), "action": "arrived"},
        )["movement"]

    def _handle_approaching(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_movement_command(
            client,
            {"station_id": station_id, "movement_id": payload.get("movement_id"), "action": "approaching"},
        )["movement"]

    def _handle_clearance_request(
        self, client: PairedClient, station_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self.application.v2_clearance_request(
            client,
            {
                "station_id": station_id,
                "movement_id": payload.get("movement_id"),
                "connection_id": payload.get("connection_id"),
                "ttl_seconds": payload.get("ttl_seconds", 120),
            },
        )["clearance"]

    def _handle_clearance_response(
        self, client: PairedClient, station_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del station_id
        return self.application.v2_clearance_respond(
            client,
            {"clearance_id": payload.get("clearance_id"), "accept": bool(payload.get("accept"))},
        )["clearance"]

    def _handle_clearance_cancel(
        self, client: PairedClient, station_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del station_id
        return self.application.v2_clearance_cancel(
            client,
            {"clearance_id": payload.get("clearance_id")},
        )["clearance"]

    def _handle_line_publish(self, client: PairedClient, station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.application.v2_line_publish(
            client,
            {
                "station_id": station_id,
                "movement_id": payload.get("movement_id"),
                "connection_id": payload.get("connection_id"),
            },
        )["line_message"]

    def _handle_line_acknowledge(
        self, client: PairedClient, station_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del station_id
        return self.application.v2_line_acknowledge(
            client,
            {"message_id": payload.get("message_id")},
        )["line_message"]
