from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .demo import demo_session
from .engine import TrafficEngine
from .identity import DeviceKind, IdentityStore
from .models import Command, CommandAck, DispatchMode
from .storage import SQLiteStateStore


LOGGER = logging.getLogger("tambox_gateway.mqtt")


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
            client_id=f"tambox-gateway-{gateway_id}",
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
        del userdata
        try:
            topic_parts = message.topic.split("/")
            if (
                len(topic_parts) == 5
                and topic_parts[:3] == ["tambox", "v1", "device"]
                and topic_parts[4] == "hello"
            ):
                self._handle_device_hello(topic_parts[3], message.payload)
                client.ack(message.mid, message.qos)
                return
            if len(topic_parts) != 5 or topic_parts[:3] != ["tambox", "v1", "client"]:
                raise ValueError("Unexpected command topic")
            client_id = topic_parts[3]
            message_kind = topic_parts[4]
            if message_kind == "presence":
                self._publish_client_snapshots(client_id)
                client.ack(message.mid, message.qos)
                return
            if message_kind != "command":
                raise ValueError("Unexpected client topic")

            payload = json.loads(message.payload.decode("utf-8"))
            if payload.get("client_id") != client_id:
                raise ValueError("Client id does not match command topic")
            paired_client = self.identities.client(client_id) if self.identities is not None else None
            gateway_clock = paired_client is not None and paired_client.kind == DeviceKind.ESP32_PANEL
            command = _decode_command(
                payload,
                received_at=datetime.now(timezone.utc),
                use_gateway_clock=gateway_clock,
            )
            if self.identities is not None and command.panel_id not in self.identities.panels_for_client(client_id):
                ack = CommandAck(
                    command_id=command.command_id,
                    status="rejected",
                    reason="panel_not_assigned",
                    previous_revision=self.engine.revision,
                    revision=self.engine.revision,
                    snapshots={},
                )
            else:
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
        device = self.identities.record_discovery(
            device_id,
            str(payload["device_code"]),
            model=str(payload.get("model", "Tambox")),
            firmware_version=str(payload.get("firmware_version", "unknown")),
        )
        assigned_panel_ids = list(self.identities.panels_for_client(device_id))
        self.client.publish(
            f"tambox/v1/device/{device_id}/assignment",
            json.dumps(
                {
                    "protocol_version": 1,
                    "status": "assigned" if assigned_panel_ids else "waiting_for_assignment",
                    "device_id": device_id,
                    "device_code": device.device_code,
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
            self.client.publish(
                f"tambox/v1/client/{client_id}/snapshot/{panel_id}",
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                qos=1,
                retain=True,
            )


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
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser(description="TrainMeet Tambox local MQTT gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--gateway-id", default="gateway-local")
    parser.add_argument(
        "--state-db",
        default="data/tambox-state.db",
        help="SQLite database used to restore the active run after restart",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in DispatchMode],
        default=DispatchMode.CLEARANCE.value,
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state_store = SQLiteStateStore(args.state_db)
    engine = TrafficEngine(
        demo_session(DispatchMode(args.mode)),
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
