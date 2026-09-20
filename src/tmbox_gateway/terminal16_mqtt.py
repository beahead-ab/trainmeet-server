"""Thin, non-retained display transport. Discovery never grants a station."""
import json
from time import monotonic

from .identity import CLIENT_ID_PATTERN, DisplayCapability


class Terminal16Gateway:
    PREFIX = "tmbox/terminal/device/"
    SUBSCRIPTIONS = tuple("tmbox/terminal/device/+/" + leaf for leaf in ("hello", "presence", "command"))

    def __init__(self, terminals, publish, *, now=monotonic):
        self.terminals, self.publish, self.now = terminals, publish, now
        self.connections = {}

    def on_message(self, topic, payload, *, retained=False):
        if retained or not topic.startswith(self.PREFIX) or len(payload) > 4096:
            return
        parts = topic[len(self.PREFIX):].split("/")
        if len(parts) != 2 or not CLIENT_ID_PATTERN.fullmatch(parts[0]):
            return
        device, leaf = parts
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeError):
            return
        if not isinstance(body, dict) or not isinstance(body.get("boot"), str) or not 1 <= len(body["boot"]) <= 64:
            return
        with self.terminals.service.operations_store.command_lock:
            if leaf == "hello":
                self._expire()
                if device not in self.connections and len(self.connections) >= 256:
                    return
                self.terminals.service.identities.record_discovery(device, str(body.get("device_code") or device),
                    model=str(body.get("model") or "TMBox 16×2")[:80], firmware_version=str(body.get("firmware_version") or "")[:32],
                    hardware_version=str(body.get("hardware_version") or "server-16x2")[:80],
                    protocol_version=2, display=DisplayCapability(rows=2, cols=16, charset="cgram"))
                self.connections[device] = {"boot": body["boot"], "seen": self.now(), "frame": None}
                self._frame(device)
            connection = self.connections.get(device)
            if not connection or connection["boot"] != body["boot"]:
                return
            connection["seen"] = self.now()
            if leaf == "presence":
                # A liveness reply is not a repeated assignment/config download.
                self.publish(self.PREFIX + device + "/alive", {"boot": body["boot"], "nonce": body.get("nonce")}, False)
                self._frame(device)
            elif leaf == "command":
                answer = self.terminals.command(device, {key: value for key, value in body.items() if key != "boot"})
                self.publish(self.PREFIX + device + "/ack", {**answer, "boot": body["boot"], "command_id": body.get("command_id")}, False)
                self.tick()

    def _expire(self):
        for device in list(self.connections):
            if self.now() - self.connections[device]["seen"] > 45:
                self.connections.pop(device)

    def _frame(self, device):
        connection = self.connections[device]
        frame = self.terminals.frame(device)
        if frame != connection["frame"]:
            self.publish(self.PREFIX + device + "/frame", {"boot": connection["boot"], "frame": frame}, False)
            connection["frame"] = frame

    def tick(self):
        with self.terminals.service.operations_store.command_lock:
            self._expire()
            for device in list(self.connections):
                self._frame(device)
