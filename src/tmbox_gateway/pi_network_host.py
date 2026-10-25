"""Linux-only privileged helper; no TCP listener and no shell execution.

Uses Raspberry Pi OS's python3-dbus (not the web server's virtualenv).
NetworkManager remains the only persistent owner of Wi-Fi credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import struct
from uuid import uuid4

from .pi_network import MAX_BYTES, SOCKET_PATH, NetworkController, NetworkError, fail

NM = "org.freedesktop.NetworkManager"
ROOT = "/org/freedesktop/NetworkManager"
SETTINGS = NM + ".Settings"
CONNECTION = SETTINGS + ".Connection"
DEVICE = NM + ".Device"
WIRELESS = DEVICE + ".Wireless"
PROPERTIES = "org.freedesktop.DBus.Properties"


class NetworkManager:
    def __init__(self):
        import dbus  # System package; importing this module elsewhere is safe.
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.manager = self.iface(ROOT, NM)
        self.settings = self.iface(ROOT + "/Settings", SETTINGS)

    def iface(self, path, interface):
        return self.dbus.Interface(self.bus.get_object(NM, path), interface)

    def props(self, path, interface):
        return self.iface(path, PROPERTIES).GetAll(interface, timeout=5)

    def devices(self):
        return [(str(path), self.props(path, DEVICE)) for path in self.manager.GetDevices(timeout=5)]

    def device(self, interface):
        for path, values in self.devices():
            if values["DeviceType"] == 2 and values["Managed"] and str(values["Interface"]) == interface:
                return path
        fail("no_wifi", "Inget hanterat Wi-Fi-nätverkskort finns med det namnet.")

    def connections(self):
        result = []
        for path in self.settings.ListConnections(timeout=5):
            config = self.iface(path, CONNECTION).GetSettings(timeout=5)
            if config.get("connection", {}).get("type") == "802-11-wireless":
                result.append((str(path), config))
        return result

    def connection(self, ident):
        for path, config in self.connections():
            if str(config["connection"]["uuid"]) == ident:
                return path, config
        fail("invalid_profile", "Nätverksprofilen finns inte längre.")

    @staticmethod
    def profile(path, config, active):
        connection = config["connection"]
        wireless = config.get("802-11-wireless", {})
        ssid = bytes(wireless.get("ssid", b"")).decode("utf-8", errors="replace")
        security = config.get("802-11-wireless-security", {})
        key = security.get("key-mgmt", "")
        kind = {"": "open", "wpa-psk": "wpa2", "sae": "wpa3"}.get(key, "unsupported")
        # Leave enterprise, hotspot, agent-owned secrets and non-UTF8 SSIDs
        # alone. Editable profiles retain all unrelated IP/DNS settings.
        editable = (kind != "unsupported" and not config.get("802-1x") and
                    wireless.get("mode", "infrastructure") == "infrastructure" and
                    not security.get("psk-flags", 0) and not connection.get("permissions") and
                    ssid.encode("utf-8") == bytes(wireless.get("ssid", b"")))
        return {"uuid": str(connection["uuid"]), "ssid": ssid, "security": kind,
                "hidden": bool(wireless.get("hidden", False)), "priority": int(connection.get("autoconnect-priority", 0)),
                "autoconnect": bool(connection.get("autoconnect", True)), "active": path in active, "editable": bool(editable)}

    def status(self):
        active = set()
        adapters = []
        networks = []
        for path, values in self.devices():
            if values["DeviceType"] != 2 or not values["Managed"]:
                continue
            connection = str(values.get("ActiveConnection", "/"))
            ident = ""
            if connection != "/":
                info = self.props(connection, NM + ".Connection.Active")
                active.add(str(info["Connection"]))
                ident = str(info["Uuid"])
            addresses = []
            for key, iface in (("Ip4Config", NM + ".IP4Config"), ("Ip6Config", NM + ".IP6Config")):
                ip_path = str(values.get(key, "/"))
                if ip_path != "/":
                    addresses.extend(str(item["address"]) for item in self.props(ip_path, iface).get("AddressData", []))
            adapters.append({"interface": str(values["Interface"]), "state": int(values["State"]), "uuid": ident, "addresses": addresses})
            wifi = self.props(path, WIRELESS)
            for ap in wifi.get("AccessPoints", []):
                item = self.props(ap, NM + ".AccessPoint")
                ssid = bytes(item["Ssid"]).decode("utf-8", errors="replace")
                if not ssid:
                    continue
                flags = int(item["RsnFlags"]) | int(item["WpaFlags"])
                kinds = []
                if flags & 0x100: kinds.append("wpa2")
                if flags & 0x400: kinds.append("wpa3")
                if not int(item["Flags"]) & 1: kinds.append("open")
                networks.append({"ssid": ssid, "security": kinds, "signal": int(item["Strength"]), "interface": str(values["Interface"])})
        profiles = [self.profile(path, config, active) for path, config in self.connections()]
        profiles.sort(key=lambda p: (-p["priority"], p["ssid"], p["uuid"]))
        radio = self.props(ROOT, NM)
        return {"profiles": profiles, "devices": adapters, "nearby": networks[:100],
                "radio_enabled": bool(radio["WirelessEnabled"] and radio["WirelessHardwareEnabled"])}

    def scan(self, interface):
        self.iface(self.device(interface), WIRELESS).RequestScan(self.dbus.Dictionary({}, signature="sv"), timeout=5)

    def save(self, ident, data):
        d = self.dbus
        if ident:
            path, config = self.connection(ident)
            config = dict(config)
            # GetSettings omits secrets. Retain them inside this process only.
            if data["security"] != "open" and not data["password"]:
                old = self.iface(path, CONNECTION).GetSecrets("802-11-wireless-security", timeout=5)
                data = {**data, "password": str(old.get("802-11-wireless-security", {}).get("psk", ""))}
                if not data["password"]:
                    fail("password_required", "Ange nätverkets lösenord igen.")
        else:
            ident = str(uuid4())
            priorities = [int(c["connection"].get("autoconnect-priority", 0)) for _, c in self.connections()]
            config = {"connection": {"uuid": ident, "type": "802-11-wireless", "autoconnect-priority": d.Int32(max(-999, min(priorities, default=1) - 1))},
                      "802-11-wireless": {}, "ipv4": {"method": "auto"}, "ipv6": {"method": "auto"}}
        config["connection"].update({"id": "TrainMeet · " + data["ssid"], "autoconnect": d.Boolean(True), "autoconnect-retries": d.Int32(2)})
        config["802-11-wireless"].update({"ssid": d.ByteArray(data["ssid"].encode("utf-8")), "mode": "infrastructure", "hidden": d.Boolean(data["hidden"])})
        if data["security"] == "open":
            config.pop("802-11-wireless-security", None)
            config["802-11-wireless"].pop("security", None)
        else:
            config["802-11-wireless"]["security"] = "802-11-wireless-security"
            config["802-11-wireless-security"] = {"key-mgmt": "sae" if data["security"] == "wpa3" else "wpa-psk",
                "proto": d.Array(["rsn"], signature="s"), "psk": data["password"], "psk-flags": d.UInt32(0),
                "pmf": d.Int32(3 if data["security"] == "wpa3" else 2)}
        typed = d.Dictionary({key: d.Dictionary(value, signature="sv") for key, value in config.items()}, signature="sa{sv}")
        if "path" in locals():
            # Update2 block-autoconnect: saving never activates an idle adapter.
            self.iface(path, CONNECTION).Update2(typed, d.UInt32(0x21), d.Dictionary({}, signature="sv"), timeout=5)
        else:
            self.settings.AddConnection2(typed, d.UInt32(0x21), d.Dictionary({}, signature="sv"), timeout=5)

    def delete(self, ident):
        path, _ = self.connection(ident)
        self.iface(path, CONNECTION).Delete(timeout=5)

    def order(self, ids):
        originals = []
        active = {p["uuid"] for p in self.status()["profiles"] if p["active"]}
        try:
            for index, ident in enumerate(ids):
                path, config = self.connection(ident)
                # GetSettings deliberately omits secrets. Merge them privately
                # before replacing settings; never risk erasing a saved PSK.
                for key, values in self.iface(path, CONNECTION).GetSecrets("", timeout=5).items():
                    config.setdefault(key, {}).update(values)
                old = dict(config["connection"])
                flags = 0x41 if ident in active else 0x61
                originals.append((path, config, old, flags))
                config["connection"] = {**old, "autoconnect-priority": self.dbus.Int32(900 - index)}
                self._update(path, config, flags)
        except Exception:
            for path, config, old, flags in reversed(originals):
                config["connection"] = old
                try:
                    self._update(path, config, flags)
                except Exception:
                    pass  # Best effort; report failure and re-read actual state.
            raise

    def _update(self, path, config, flags):
        d = self.dbus
        typed = d.Dictionary({key: d.Dictionary(value, signature="sv") for key, value in config.items()}, signature="sa{sv}")
        self.iface(path, CONNECTION).Update2(typed, d.UInt32(flags), d.Dictionary({}, signature="sv"), timeout=5)

    def connected(self, interface, ident):
        return any(d["interface"] == interface and d["uuid"] == ident and d["state"] == 100 and d["addresses"] for d in self.status()["devices"])

    def checkpoint(self, device, seconds):
        return str(self.manager.CheckpointCreate(self.dbus.Array([device], signature="o"), self.dbus.UInt32(seconds), self.dbus.UInt32(0), timeout=5))

    def checkpoint_exists(self, path):
        return path in self.props(ROOT, NM)["Checkpoints"]

    def has_checkpoints(self):
        return bool(self.props(ROOT, NM)["Checkpoints"])

    def activate(self, ident, device):
        path, _ = self.connection(ident)
        self.manager.ActivateConnection(path, device, "/", timeout=5)

    def commit_checkpoint(self, path):
        self.manager.CheckpointDestroy(path, timeout=5)

    def rollback(self, path):
        results = self.manager.CheckpointRollback(path, timeout=5)
        if any(int(value) != 0 for value in results.values()):
            fail("rollback_failed", "Nätverket kunde inte återställas helt. Kontrollera anslutningen på Pi:n.")


class HostHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        if uid != self.server.allowed_uid:
            return
        try:
            raw = self.rfile.readline(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
                raise ValueError
            message = json.loads(raw)
            if not isinstance(message, dict) or set(message) != {"action", "payload", "actor"}:
                raise ValueError
            result = self.server.controller.handle(message["action"], message["payload"], message["actor"])
        except NetworkError as error:
            result = {"error": error.code, "message": str(error)}
        except Exception:
            # D-Bus errors can contain settings/secrets. Never echo or log them.
            result = {"error": "network_failed", "message": "Nätverksåtgärden misslyckades. Kontrollera nätverkskort, säkerhetstyp och lösenord."}
        try:
            self.wfile.write(json.dumps(result).encode() + b"\n")
        except OSError:
            pass  # The NetworkManager rollback timer still runs.


def main():
    model = Path("/proc/device-tree/model")
    if not model.exists() or b"Raspberry Pi" not in model.read_bytes():
        raise SystemExit("Nätverkstjänsten är endast avsedd för Raspberry Pi.")
    account = pwd.getpwnam("trainmeet-server")
    path = Path(SOCKET_PATH)
    path.unlink(missing_ok=True)  # Parent is a root-owned systemd RuntimeDirectory.
    os.umask(0o007)
    with socketserver.UnixStreamServer(str(path), HostHandler) as server:
        os.chown(path, 0, account.pw_gid)
        os.chmod(path, 0o660)
        server.allowed_uid = account.pw_uid
        server.controller = NetworkController(NetworkManager())
        server.serve_forever()


if __name__ == "__main__":
    main()
