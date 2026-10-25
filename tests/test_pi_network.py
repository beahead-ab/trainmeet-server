from copy import deepcopy
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pi_network_fixture import FakeNetwork, HOME, MEET
from tmbox_gateway.pi_network import NetworkController, NetworkError, host_request, validate_profile
from tmbox_gateway.pi_network_host import NetworkManager, HostHandler


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeNetwork()
        self.now = 100
        self.controller = NetworkController(self.backend, lambda: self.now)

    def call(self, action, payload=None, actor="owner"):
        return self.controller.handle(action, payload or {}, actor)

    def denied(self, code, action, payload):
        with self.assertRaises(NetworkError) as error: self.call(action, payload)
        self.assertEqual(error.exception.code, code)

    def connect(self):
        return self.call("connect", dict(uuid=MEET, interface="wlan0"))

    def test_status_never_contains_secrets(self):
        result = json.dumps(self.call("status"))
        self.assertNotIn("password", result)
        self.assertNotIn("private-", result)

    def test_save_does_not_activate_and_blank_retains_password(self):
        self.call("save", dict(uuid=MEET, ssid="Träffens nät", security="wpa3", password="", hidden=True))
        self.assertEqual(self.backend.secrets[MEET], "private-meet")
        self.assertTrue(self.backend.profiles[0]["active"])
        self.assertEqual(self.backend.calls, [])

    def test_ssid_or_security_change_requires_new_password(self):
        for values in (dict(ssid="Annat", security="wpa3"), dict(ssid="Träffens nät", security="wpa2")):
            self.denied("invalid_password", "save", dict(uuid=MEET, **values))

    def test_active_profile_cannot_be_changed_or_deleted(self):
        self.denied("active_profile", "delete", dict(uuid=HOME))
        self.denied("active_profile", "save", dict(uuid=HOME, ssid="Hemma", security="wpa2"))

    def test_validation_byte_limits_and_controls_and_unknown_fields(self):
        for data in (dict(ssid="å" * 17, security="open"), dict(ssid="x\ny", security="open"),
                     dict(ssid="", security="open"), dict(ssid="ok", security="wep"),
                     dict(ssid="ok", security="open", hidden="false"),
                     dict(ssid="ok", security="wpa2", password="short"),
                     dict(ssid="ok", security="wpa2", password="x" * 64),
                     dict(ssid="ok", security="wpa2", password="\n" * 10),
                     dict(ssid="ok", security="open", command="reboot"),
                     dict(ssid="ok", security="open", password="unexpected")):
            with self.subTest(data=data), self.assertRaises(NetworkError): validate_profile(data)
        self.assertEqual(validate_profile(dict(ssid="å" * 16, security="wpa2", password="x" * 63))["ssid"], "å" * 16)

    def test_save_duplicate_and_profile_limit(self):
        self.denied("duplicate_ssid", "save", dict(ssid="Hemma", security="open"))
        self.backend.profiles *= 16
        self.denied("profile_limit", "save", dict(ssid="Nytt", security="open"))

    def test_noneditable_profiles_are_protected(self):
        self.backend.profiles[1]["editable"] = False
        for action in ("save", "delete", "connect"):
            payload = dict(uuid=MEET, interface="wlan0") if action == "connect" else dict(uuid=MEET)
            self.denied("invalid_profile", action, payload)

    def test_order_exact_ids_and_persists_without_activation(self):
        for ids in ([MEET], [HOME, HOME], [MEET, "foreign"], [None, HOME]):
            self.denied("stale_profiles", "order", dict(uuids=ids))
        self.call("order", dict(uuids=[MEET, HOME]))
        self.assertEqual([p["uuid"] for p in self.backend.profiles], [MEET, HOME])
        self.assertTrue(self.backend.connected("wlan0", HOME))

    def test_scan_is_rate_limited_and_interface_is_checked(self):
        self.call("scan", dict(interface="wlan0")); self.call("scan", dict(interface="wlan0"))
        self.assertEqual(len(self.backend.calls), 1)
        self.now += 11
        self.call("scan", dict(interface="wlan0"))
        self.assertEqual(len(self.backend.calls), 2)
        self.denied("no_wifi", "scan", dict(interface="eth0"))

    def test_trial_uses_os_checkpoint_and_confirm_requires_connected_ip(self):
        result = self.connect()
        self.assertEqual(self.backend.calls[0], ("checkpoint", "/wifi/0", 90))
        self.backend.ready = False
        self.denied("not_connected", "confirm", dict(token=result["trial"]["token"]))
        self.backend.ready = True
        self.assertIsNone(self.call("confirm", dict(token=result["trial"]["token"]))["trial"])
        self.assertFalse(self.backend.has_checkpoints())
        self.assertTrue(self.backend.connected("wlan0", MEET))

    def test_trial_owner_and_retry_and_mutation_lock(self):
        result = self.connect()
        self.assertEqual(self.connect()["trial"], result["trial"])
        self.assertIsNone(self.call("status", actor="other")["trial"]["token"])
        with self.assertRaises(NetworkError): self.call("confirm", dict(token=result["trial"]["token"]), actor="other")
        self.denied("trial_pending", "delete", dict(uuid=HOME))
        self.denied("invalid_trial", "rollback", dict(token="incorrect"))
        self.call("rollback", dict(token=result["trial"]["token"]))
        self.assertTrue(self.backend.connected("wlan0", HOME))

    def test_timeout_and_helper_restart_never_cancel_os_timer(self):
        result = self.connect()
        self.now += 91
        status = self.call("status")
        self.assertIsNone(status["trial"])
        self.assertTrue(status["recovering"])
        self.denied("invalid_trial", "confirm", dict(token=result["trial"]["token"]))
        self.controller = NetworkController(self.backend)
        self.denied("network_recovering", "order", dict(uuids=[HOME, MEET]))
        self.backend.rollback("/checkpoint/1")  # Simulated NM-owned timeout.
        self.assertFalse(self.call("status")["recovering"])

    def test_activation_failure_rolls_back_and_redacts_error(self):
        self.backend.fail_activation = True
        with self.assertRaises(NetworkError) as error: self.connect()
        self.assertEqual(error.exception.code, "activation_failed")
        self.assertNotIn("secret-error", str(error.exception))
        self.assertTrue(self.backend.connected("wlan0", HOME))
        self.assertIsNone(self.controller.pending)

    def test_unsupported_host_is_safe(self):
        with patch("tmbox_gateway.pi_network.socket.socket", side_effect=OSError("not installed")):
            self.assertFalse(host_request()["supported"])
            with self.assertRaises(NetworkError): host_request("save", {})


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.nm = NetworkManager.__new__(NetworkManager)
        self.nm.dbus = SimpleNamespace(Dictionary=lambda x, **kw: dict(x), Array=lambda x, **kw: list(x),
                                      Int32=int, UInt32=int, Boolean=bool, ByteArray=bytes)
        self.config = {"connection": {"uuid": HOME, "type": "802-11-wireless", "id": "original", "autoconnect-priority": 2},
                       "802-11-wireless": {"ssid": b"Hemma", "mode": "infrastructure"},
                       "802-11-wireless-security": {"key-mgmt": "wpa-psk", "psk-flags": 0},
                       "ipv4": {"method": "manual", "address-data": [{"address": "192.168.1.9", "prefix": 24}]}}
        self.api = Mock()
        self.api.GetSecrets.return_value = {"802-11-wireless-security": {"psk": "must-stay-private"}}
        self.nm.iface = Mock(return_value=self.api)
        self.nm.connection = Mock(side_effect=lambda _: ("/profile/1", deepcopy(self.config)))
        self.nm.connections = Mock(return_value=[("/profile/1", deepcopy(self.config))])
        self.nm.settings = Mock()
        self.nm.status = Mock(return_value={"profiles": [{"uuid": HOME, "active": True}]})

    def test_public_profile_allowlist_and_noneditable_security(self):
        config = deepcopy(self.config)
        config["802-11-wireless-security"]["psk"] = "do-not-return"
        public = self.nm.profile("/profile/1", config, {"/profile/1"})
        self.assertTrue(public["editable"])
        self.assertNotIn("do-not-return", json.dumps(public))
        for key, value in (("key-mgmt", "wpa-eap"), ("psk-flags", 1)):
            changed = deepcopy(config); changed["802-11-wireless-security"][key] = value
            self.assertFalse(self.nm.profile("x", changed, set())["editable"])
        config["802-11-wireless"]["mode"] = "ap"
        self.assertFalse(self.nm.profile("x", config, set())["editable"])

    def test_save_preserves_ip_and_secret_with_autoconnect_blocked(self):
        self.nm.save(HOME, dict(ssid="Hemma", security="wpa2", password="", hidden=True))
        config, flags, args = self.api.Update2.call_args.args
        self.assertEqual(flags, 0x21)
        self.assertEqual(config["ipv4"], self.config["ipv4"])
        self.assertEqual(config["802-11-wireless-security"]["psk"], "must-stay-private")
        self.assertTrue(config["802-11-wireless"]["hidden"])

    def test_new_profile_is_persistent_and_append_priority(self):
        self.nm.save(None, dict(ssid="Klubb", security="wpa3", password="long-secret", hidden=False))
        config, flags, args = self.nm.settings.AddConnection2.call_args.args
        self.assertEqual(flags, 0x21)
        self.assertEqual(config["connection"]["autoconnect-priority"], 1)
        self.assertEqual(config["802-11-wireless-security"]["key-mgmt"], "sae")
        self.assertEqual(config["ipv4"]["method"], "auto")

    def test_order_retains_secrets_and_active_reconnect_eligibility(self):
        self.nm.order([HOME])
        config, flags, _ = self.api.Update2.call_args.args
        self.assertEqual(flags, 0x41)
        self.assertEqual(config["802-11-wireless-security"]["psk"], "must-stay-private")
        self.assertEqual(config["connection"]["autoconnect-priority"], 900)
        self.nm.status.return_value = {"profiles": []}
        self.nm.order([HOME])
        self.assertEqual(self.api.Update2.call_args.args[1], 0x61)

    def test_failed_reorder_attempts_restore(self):
        self.api.Update2.side_effect = [RuntimeError("network lost"), None]
        with self.assertRaises(RuntimeError): self.nm.order([HOME])
        self.assertEqual(self.api.Update2.call_count, 2)
        self.assertEqual(self.api.Update2.call_args.args[0]["connection"]["autoconnect-priority"], 2)


class PrivilegeBoundaryTests(unittest.TestCase):
    def handler(self, uid, raw, controller=None):
        handler = HostHandler.__new__(HostHandler)
        handler.request = Mock()
        handler.request.getsockopt.return_value = struct.pack("3i", 1, uid, 100)
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.server = SimpleNamespace(allowed_uid=100, controller=controller or Mock())
        with patch("socket.SO_PEERCRED", 17, create=True): handler.handle()
        return handler

    def test_only_the_web_service_uid_can_reach_the_controller(self):
        handler = self.handler(501, b'{}\n')
        handler.server.controller.handle.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), b'')

    def test_root_helper_never_returns_raw_dbus_errors(self):
        controller = Mock()
        controller.handle.side_effect = RuntimeError("psk=really-private-password")
        body = json.dumps(dict(action="save", actor="owner", payload={})).encode() + b'\n'
        handler = self.handler(100, body, controller)
        response = handler.wfile.getvalue()
        self.assertNotIn(b"really-private", response)
        self.assertEqual(json.loads(response)["error"], "network_failed")

    def test_incomplete_oversize_and_unknown_envelopes_are_rejected(self):
        for raw in (b'{}', b'x' * 65537 + b'\n', b'{"command":"reboot"}\n'):
            handler = self.handler(100, raw)
            handler.server.controller.handle.assert_not_called()
            self.assertEqual(json.loads(handler.wfile.getvalue())["error"], "network_failed")

    def test_installer_only_enables_helper_on_pi_with_existing_networkmanager(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/install-raspberry-pi.sh").read_text()
        self.assertIn("systemctl is-active --quiet NetworkManager.service", script)
        self.assertIn("EXTRA_PACKAGES=python3-dbus", script)
        self.assertNotIn("apt-get install network-manager", script)
        service = (root / "packaging/raspberry-pi/trainmeet-network.service").read_text()
        self.assertIn("RestrictAddressFamilies=AF_UNIX", service)
        self.assertIn("ProtectSystem=strict", service)
        web = (root / "packaging/raspberry-pi/trainmeet-server.service").read_text()
        self.assertIn("User=trainmeet-server", web)


if __name__ == "__main__": unittest.main()
