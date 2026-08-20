"""MQTT surface for TMBox protocol v2.

The transport is deliberately thin. Everything a box needs to reach a correct
state after a reconnect sits in three retained topics - assignment, config and
snapshot - which is the whole synchronisation mechanism. There is no event
replay and no bridge to the v1 panel protocol; the two run on separate
prefixes so an older device can never mistake one for the other.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .identity import DisplayCapability, IdentityStore
from .protocol_v2 import PROTOCOL_VERSION, TMBoxStationService


LOGGER = logging.getLogger("tmbox_gateway.mqtt_v2")

TOPIC_PREFIX = "tmbox/v2"

Publish = Callable[[str, dict[str, Any], bool], None]


def device_topic(device_id: str, leaf: str) -> str:
    return f"{TOPIC_PREFIX}/device/{device_id}/{leaf}"


def gateway_status_topic(gateway_id: str) -> str:
    return f"{TOPIC_PREFIX}/gateway/{gateway_id}/status"


class TMBoxV2Gateway:
    """Routes v2 topics to the station service and publishes what comes back.

    Transport-independent on purpose: it is handed a publish callable, so the
    whole surface can be exercised without a broker.
    """

    SUBSCRIPTIONS = (
        f"{TOPIC_PREFIX}/device/+/hello",
        f"{TOPIC_PREFIX}/device/+/presence",
        f"{TOPIC_PREFIX}/device/+/command",
        f"{TOPIC_PREFIX}/device/+/config/ack",
    )

    def __init__(
        self,
        service: TMBoxStationService,
        identities: IdentityStore,
        *,
        gateway_id: str,
        publish: Publish,
    ):
        self.service = service
        self.identities = identities
        self.gateway_id = gateway_id
        self.publish = publish

    # ------------------------------------------------------------- lifecycle

    def announce_online(self) -> None:
        self.publish(
            gateway_status_topic(self.gateway_id),
            {"protocol_version": PROTOCOL_VERSION, "status": "online", "gateway_id": self.gateway_id},
            True,
        )

    def offline_will(self) -> tuple[str, dict[str, Any]]:
        return (
            gateway_status_topic(self.gateway_id),
            {"protocol_version": PROTOCOL_VERSION, "status": "offline", "gateway_id": self.gateway_id},
        )

    # -------------------------------------------------------------- messages

    def on_message(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 5 or parts[0:3] != ["tmbox", "v2", "device"]:
            LOGGER.warning("Okänt v2-topic: %s", topic)
            return
        device_id = parts[3]
        leaf = "/".join(parts[4:])
        try:
            body = json.loads(payload.decode("utf-8")) if payload else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Ogiltig payload på %s", topic)
            return
        if not isinstance(body, dict):
            LOGGER.warning("Payload på %s är inte ett objekt", topic)
            return

        if leaf == "hello":
            self.handle_hello(device_id, body)
        elif leaf == "presence":
            self.handle_presence(device_id, body)
        elif leaf == "command":
            self.handle_command(device_id, body)
        elif leaf == "config/ack":
            LOGGER.info(
                "TMBox %s kvitterade config %s", device_id, body.get("config_version")
            )
        else:
            LOGGER.warning("Okänt v2-topic: %s", topic)

    def handle_hello(self, device_id: str, body: dict[str, Any]) -> None:
        self.identities.record_discovery(
            device_id,
            str(body.get("device_code") or device_id),
            model=str(body.get("model") or "TMBox"),
            firmware_version=str(body.get("firmware_version") or "unknown"),
            hardware_version=str(body.get("hardware_version") or ""),
            protocol_version=int(body.get("protocol_version") or PROTOCOL_VERSION),
            display=DisplayCapability.parse(body.get("display")),
        )
        self.publish_device_state(device_id)

    def handle_presence(self, device_id: str, body: dict[str, Any]) -> None:
        if str(body.get("status") or "online") != "online":
            return
        # Retained topics already hold a complete state, but republishing on
        # presence costs nothing and closes the window where a box connected
        # before its station was assigned.
        self.publish_device_state(device_id)

    def handle_command(self, device_id: str, body: dict[str, Any]) -> None:
        acknowledgement = self.service.handle_command(device_id, body)
        self.publish(device_topic(device_id, "ack"), acknowledgement, False)
        station_id = self.identities.station_for_client(device_id)
        if station_id and acknowledgement["status"] == "accepted":
            self.publish_station_snapshot(station_id)

    # ------------------------------------------------------------ publishing

    def publish_device_state(self, device_id: str) -> None:
        """Publish the three retained topics a box resynchronises from.

        They may arrive at the box in any order; each carries a complete
        state, so none of them depends on another having arrived first.
        """
        assignment = self.service.assignment_payload(device_id)
        self.publish(device_topic(device_id, "assignment"), assignment, True)
        station_id = assignment.get("station_id")
        if not station_id:
            return
        device = self.identities.discovered_device_or_none(device_id)
        config = self.service.config_payload(
            station_id, device.display if device else None
        )
        if config is not None:
            self.publish(device_topic(device_id, "config"), config, True)
        snapshot = self.service.snapshot_payload(station_id)
        if snapshot is not None:
            self.publish(device_topic(device_id, "snapshot"), snapshot, True)

    def publish_station_snapshot(self, station_id: str) -> None:
        """Every box in the same operating room sees the same station state."""
        snapshot = self.service.snapshot_payload(station_id)
        if snapshot is None:
            return
        for client in self.identities.enabled_clients():
            if client.station_id == station_id:
                self.publish(device_topic(client.client_id, "snapshot"), snapshot, True)


class MQTTV2Adapter:
    """Paho transport for the v2 gateway, running beside the v1 adapter.

    Separate client, separate prefix, no bridge. The two protocols share a
    broker and nothing else.
    """

    def __init__(
        self,
        gateway: TMBoxV2Gateway,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
    ):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:  # pragma: no cover - exercised by deployment
            raise RuntimeError(
                "Install the mqtt optional dependency: pip install -e '.[mqtt]'"
            ) from error

        self.gateway = gateway
        self.host = host
        self.port = port
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"tmbox-gateway-v2-{gateway.gateway_id}",
            protocol=mqtt.MQTTv5,
            reconnect_on_failure=True,
        )
        will_topic, will_payload = gateway.offline_will()
        self.client.will_set(will_topic, payload=_encode(will_payload), qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        gateway.publish = self._publish

    def connect(self) -> None:
        self.client.connect(self.host, self.port, keepalive=10, clean_start=True)
        self.client.loop_start()

    def disconnect(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def _publish(self, topic: str, payload: dict[str, Any], retain: bool) -> None:
        self.client.publish(topic, _encode(payload), qos=1, retain=retain)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            LOGGER.error("Broker avvisade v2-anslutningen: %s", reason_code)
            return
        for subscription in TMBoxV2Gateway.SUBSCRIPTIONS:
            client.subscribe(subscription, qos=1)
        self.gateway.announce_online()
        LOGGER.info("TMBox-gateway v2 online")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        del client, userdata
        try:
            self.gateway.on_message(message.topic, message.payload)
        except Exception:  # pragma: no cover - defensive transport boundary
            LOGGER.exception("Ett v2-meddelande kunde inte hanteras: %s", message.topic)


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
