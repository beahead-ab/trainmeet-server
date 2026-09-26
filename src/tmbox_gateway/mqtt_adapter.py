from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine import TrafficEngine
from .identity import DeviceKind, DisplayCapability, IdentityStore
from .models import Command, CommandAck, unconfigured_session
from .observability import configure_logging
from .storage import SQLiteStateStore
from .device_ui import ui_payload


LOGGER = logging.getLogger("tmbox_gateway.mqtt")


class MQTTGatewayAdapter:
    """MQTT 5 transport around the authoritative traffic engine."""

    def __init__(
        self,
        engine: TrafficEngine,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        gateway_id: str = "gateway-local",
        identities: IdentityStore | None = None,
    ):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:  # pragma: no cover - exercised by deployment
            raise RuntimeError("Install the mqtt optional dependency: pip install -e '.[mqtt]'") from error

        self.mqtt = mqtt
        self.engine = engine
        self.host = host
        self.port = port
        self.gateway_id = gateway_id
        self.identities = identities
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"tmbox-gateway-{gateway_id}",
            protocol=mqtt.MQTTv5,
            reconnect_on_failure=True,
            manual_ack=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.will_set(
            f"tambox/v1/gateway/{gateway_id}/status",
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
        client.subscribe("tambox/v1/client/+/command", qos=1)
        client.subscribe("tambox/v1/client/+/presence", qos=1)
        client.subscribe("tambox/v1/device/+/hello", qos=1)
        client.subscribe("tambox/v1/device/+/preferences/set", qos=1)
        client.publish(
            f"tambox/v1/gateway/{self.gateway_id}/status",
            json.dumps({"status": "online"}),
            qos=1,
            retain=True,
        )
        self._publish_snapshots()
        LOGGER.info("Gateway online")

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        LOGGER.warning("Gateway disconnected from broker: %s", reason_code)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        with self.engine._lock:
            return self._on_message_locked(client, userdata, message)

    def _on_message_locked(self, client: Any, userdata: Any, message: Any) -> None:
        del userdata
        try:
            topic_parts = message.topic.split("/")
            if len(topic_parts) == 6 and topic_parts[:3] == ["tambox", "v1", "device"] and topic_parts[4:] == ["preferences", "set"]:
                if not message.retain and self.identities is not None:
                    body = json.loads(message.payload.decode("utf-8"))
                    request_id = body.get("request_id")
                    if isinstance(request_id, str) and 1 <= len(request_id) <= 96:
                        try:
                            self.identities.set_device_language(topic_parts[3], body.get("language"))
                            status = "accepted"
                        except ValueError:
                            status = "rejected"
                        self._publish_device_ui(topic_parts[3], request_id=request_id, status=status)
                        if status == "accepted":
                            self._publish_client_snapshots(topic_parts[3])
                client.ack(message.mid, message.qos)
                return
            if (
                len(topic_parts) == 5
                and topic_parts[:3] == ["tambox", "v1", "device"]
                and topic_parts[4] == "hello"
            ):
                self._handle_device_hello(topic_parts[3], message.payload)
                if not message.retain and self.engine.shared_traffic:
                    self.engine.shared_traffic.service.observe_operator(topic_parts[3])
                client.ack(message.mid, message.qos)
                return
            if len(topic_parts) != 5 or topic_parts[:3] != ["tambox", "v1", "client"]:
                raise ValueError("Unexpected command topic")
            client_id = topic_parts[3]
            message_kind = topic_parts[4]
            if message_kind == "presence":
                self._handle_presence(client_id, message.payload, retained=bool(message.retain))
                client.ack(message.mid, message.qos)
                return
            if message_kind != "command":
                raise ValueError("Unexpected client topic")

            payload = json.loads(message.payload.decode("utf-8"))
            if payload.get("client_id") != client_id:
                raise ValueError("Client id does not match command topic")
            # Revoke before decoding: physical boxes omit wall-clock fields.
            # A removed box is no longer a paired client, but still deserves
            # an explicit refusal instead of a misleading malformed-message log.
            if self.identities is not None and str(payload.get("panel_id") or "") not in self.identities.panels_for_client(client_id):
                ack = CommandAck(
                    command_id=str(payload.get("command_id") or ""),
                    status="rejected",
                    reason="panel_not_assigned",
                    previous_revision=self.engine.revision,
                    revision=self.engine.revision,
                    snapshots={},
                )
            else:
                paired_client = self.identities.client(client_id) if self.identities is not None else None
                command = _decode_command(
                    payload,
                    received_at=datetime.now(timezone.utc),
                    use_gateway_clock=paired_client is not None and paired_client.kind == DeviceKind.ESP32_PANEL,
                )
                ack = self.engine.press(command)
            ack_payload = ack.to_dict()
            if self.identities is not None:
                assigned = set(self.identities.panels_for_client(client_id))
                ack_payload["snapshots"] = {
                    panel_id: snapshot
                    for panel_id, snapshot in ack_payload["snapshots"].items()
                    if panel_id in assigned
                }
            client.publish(
                f"tambox/v1/client/{client_id}/ack",
                json.dumps(ack_payload, ensure_ascii=False, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            self._publish_snapshots()
            client.ack(message.mid, message.qos)
        except Exception as error:  # pragma: no cover - defensive transport boundary
            LOGGER.exception("Rejected malformed MQTT command: %s", error)
            client.ack(message.mid, message.qos)

    def _handle_device_hello(self, device_id: str, raw_payload: bytes) -> None:
        if self.identities is None:
            return
        payload = json.loads(raw_payload.decode("utf-8"))
        self.identities.record_discovery(
            device_id,
            str(payload["device_code"]),
            model=str(payload.get("model", "TMBox")),
            firmware_version=str(payload.get("firmware_version", "unknown")),
            hardware_version=str(payload.get("hardware_version", "")),
            protocol_version=int(payload.get("protocol_version", 1) or 1),
            display=DisplayCapability.parse(payload.get("display")),
        )
        self.publish_device_assignment(device_id)

    def _publish_device_ui(self, device_id: str, **extra: Any) -> None:
        if self.identities is None:
            return
        self.client.publish(
            f"tambox/v1/device/{device_id}/preferences",
            json.dumps({**extra, "ui": ui_payload(self.identities.device_language(device_id), legacy=True)},
                       ensure_ascii=False, separators=(",", ":")), qos=1, retain=False,
        )

    def publish_device_language(self, device_id: str) -> None:
        with self.engine._lock:
            self._publish_device_ui(device_id)
            self._publish_client_snapshots(device_id)

    def _handle_presence(self, client_id: str, raw_payload: bytes, *, retained: bool) -> None:
        payload = json.loads(raw_payload.decode("utf-8"))
        if payload.get("status") != "online":
            return
        if not retained and self.engine.shared_traffic:
            self.engine.shared_traffic.service.observe_operator(client_id)
        request_id = payload.get("request_id")
        if request_id is None:
            # Existing v1 clients use presence to ask for a fresh snapshot.
            self._publish_client_snapshots(client_id)
            return
        if retained or not isinstance(request_id, str) or not 1 <= len(request_id) <= 96:
            return  # A saved broker message is not evidence of a live device.
        if self.identities is None:
            return
        self.identities.touch_discovered_device(client_id)
        panels = self.identities.panels_for_client(client_id)
        panel_id = payload.get("panel_id", "")
        if panel_id not in panels:
            if panel_id or panels:
                # Recover a missed admin change; never grant what the box asks for.
                self.publish_device_assignment(client_id)
                return
            reply = {"status": "waiting_for_assignment", "request_id": request_id}
        else:
            snapshot = (self.engine.snapshot(panel_id, language=self.identities.device_language(client_id))
                        if panel_id in self.engine.config.panels else None)
            if snapshot is None:
                return  # Invalid config must not keep stale input alive.
            token = _snapshot_token(snapshot)
            if payload.get("state_token") != token:
                self._publish_client_snapshots(client_id)
                return
            reply = {
                "status": "current", "request_id": request_id,
                "panel_id": panel_id, "state_token": token,
            }
        self.client.publish(
            f"tambox/v1/client/{client_id}/state",
            json.dumps(reply, ensure_ascii=False, separators=(",", ":")),
            qos=1, retain=False,
        )

    def publish_device_assignment(self, device_id: str) -> None:
        if self.identities is None:
            return
        device = self.identities.discovered_device_or_none(device_id)
        if device is None:
            return
        self._publish_device_ui(device_id)
        assigned_panel_ids = list(self.identities.panels_for_client(device_id))
        station_id = self.identities.station_for_client(device_id)
        # Admin assigns a station now. A v1 keypad still needs one concrete
        # A-D panel for both snapshots and command authorization. Resolve only
        # an unambiguous panel of that already-authorized station; never choose
        # an arbitrary panel, change an explicit assignment, or revive a
        # disabled client (station_for_client returns None in that case).
        if device.protocol_version == 1 and station_id and not assigned_panel_ids:
            candidates = [
                panel.id for panel in self.engine.config.panels.values()
                if panel.station_id == station_id
            ]
            if len(candidates) == 1:
                self.identities.bind_legacy_station_panel(
                    device_id, station_id, candidates[0],
                )
                assigned_panel_ids = list(self.identities.panels_for_client(device_id))
                station_id = self.identities.station_for_client(device_id)
        self.client.publish(
            f"tambox/v1/device/{device_id}/assignment",
            json.dumps(
                {
                    "protocol_version": 1,
                    "status": (
                        "assigned" if (station_id or assigned_panel_ids)
                        else "waiting_for_assignment"
                    ),
                    "device_id": device_id,
                    "device_code": device.device_code,
                    "station_id": station_id,
                    "assigned_panel_ids": assigned_panel_ids,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            qos=1,
            retain=True,
        )
        if assigned_panel_ids:
            self._publish_client_snapshots(device_id)

    def _publish_snapshots(self) -> None:
        if self.identities is not None:
            for paired_client in self.identities.enabled_clients():
                self._publish_client_snapshots(paired_client.client_id)
            return
        for panel_id, snapshot in self.engine.snapshots().items():
            self.client.publish(
                f"tambox/v1/panel/{panel_id}/snapshot",
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                qos=1,
                retain=True,
            )

    def _publish_client_snapshots(self, client_id: str) -> None:
        if self.identities is None:
            return
        snapshots = self.engine.snapshots()
        for panel_id in self.identities.panels_for_client(client_id):
            snapshot = snapshots.get(panel_id)
            if snapshot is None:
                continue
            snapshot = self.engine.snapshot(panel_id, language=self.identities.device_language(client_id))
            snapshot = {**snapshot, "state_token": _snapshot_token(snapshot)}
            self.client.publish(
                f"tambox/v1/client/{client_id}/snapshot/{panel_id}",
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                qos=1,
                retain=True,
            )


def _snapshot_token(snapshot: dict[str, Any]) -> str:
    """Compare the complete authoritative view, including clock and session.

    Revision alone does not change when the meeting clock advances or stops.
    This is a content fingerprint, not a credential or an authorization grant.
    """
    return hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _decode_command(
    payload: dict[str, Any],
    *,
    received_at: datetime | None = None,
    use_gateway_clock: bool = False,
) -> Command:
    if payload.get("protocol_version") != 1:
        raise ValueError("Unsupported protocol version")
    if payload.get("action") != "key_press":
        raise ValueError("Unsupported command action")
    if use_gateway_clock:
        sent_at = received_at or datetime.now(timezone.utc)
        expires_at = sent_at + timedelta(seconds=5)
    else:
        sent_at = _parse_datetime(payload["sent_at"])
        expires_at = _parse_datetime(payload["expires_at"])
    return Command(
        command_id=str(payload["command_id"]),
        client_id=str(payload["client_id"]),
        traffic_session_id=str(payload["traffic_session_id"]),
        panel_id=str(payload["panel_id"]),
        expected_revision=int(payload["expected_revision"]),
        key=str(payload["key"]),
        sent_at=sent_at,
        expires_at=expires_at,
        train_number=payload.get("train_number"),
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser(description="TrainMeet TMBox local MQTT gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--gateway-id", default="gateway-local")
    parser.add_argument(
        "--state-db",
        default="data/tmbox-state.db",
        help="SQLite database used to restore the active run after restart",
    )
    args = parser.parse_args()

    configure_logging(logging.INFO)
    state_store = SQLiteStateStore(args.state_db)
    engine = TrafficEngine(
        unconfigured_session(),
        state_store=state_store,
    )
    adapter = MQTTGatewayAdapter(
        engine,
        host=args.host,
        port=args.port,
        gateway_id=args.gateway_id,
    )
    try:
        adapter.run_forever()
    except KeyboardInterrupt:
        LOGGER.info("Gateway stopped")
    finally:
        state_store.close()


if __name__ == "__main__":
    main()
