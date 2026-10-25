"""No host networking: deterministic backend for controller and browser tests."""
from copy import deepcopy
from uuid import uuid4

HOME = "11111111-1111-4111-8111-111111111111"
MEET = "22222222-2222-4222-8222-222222222222"


class FakeNetwork:
    def __init__(self):
        self.profiles = [
            dict(uuid=HOME, ssid="Hemma", security="wpa2", hidden=False, priority=10, autoconnect=True, active=True, editable=True),
            dict(uuid=MEET, ssid="Träffens nät", security="wpa3", hidden=False, priority=0, autoconnect=True, active=False, editable=True),
        ]
        self.secrets = {HOME: "private-home", MEET: "private-meet"}
        self.calls = []
        self.checkpoints = {}
        self.fail_activation = False
        self.ready = True

    def status(self):
        active = next((p["uuid"] for p in self.profiles if p["active"]), "")
        return deepcopy(dict(profiles=self.profiles, radio_enabled=True,
            devices=[dict(interface="wlan0", uuid=active, state=100 if self.ready else 40, addresses=["192.168.1.20"] if self.ready else [])],
            nearby=[dict(ssid="Klubblokalen", security=["wpa2"], signal=80, interface="wlan0")]))

    def device(self, interface):
        from tmbox_gateway.pi_network import fail
        if interface != "wlan0": fail("no_wifi", "Inget hanterat Wi-Fi-nätverkskort finns med det namnet.")
        return "/wifi/0"

    def scan(self, interface): self.calls.append(("scan", interface))
    def save(self, ident, data):
        ident = ident or str(uuid4())
        old = next((p for p in self.profiles if p["uuid"] == ident), None)
        if data["password"]: self.secrets[ident] = data["password"]
        values = {k: v for k, v in data.items() if k != "password"}
        if old: old.update(values)
        else: self.profiles.append(dict(uuid=ident, priority=-1, active=False, editable=True, autoconnect=True, **values))

    def delete(self, ident):
        self.profiles = [p for p in self.profiles if p["uuid"] != ident]
        self.secrets.pop(ident, None)

    def order(self, ids):
        for index, ident in enumerate(ids):
            next(p for p in self.profiles if p["uuid"] == ident)["priority"] = 900 - index
        self.profiles.sort(key=lambda p: -p["priority"])

    def checkpoint(self, device, seconds):
        self.calls.append(("checkpoint", device, seconds))
        self.checkpoints["/checkpoint/1"] = deepcopy(self.profiles)
        return "/checkpoint/1"

    def checkpoint_exists(self, path): return path in self.checkpoints
    def has_checkpoints(self): return bool(self.checkpoints)
    def activate(self, ident, device):
        if self.fail_activation: raise RuntimeError("secret-error-not-for-browser")
        self.calls.append(("activate", ident, device))
        for p in self.profiles: p["active"] = p["uuid"] == ident

    def connected(self, interface, ident):
        return self.ready and any(p["uuid"] == ident and p["active"] for p in self.profiles)

    def commit_checkpoint(self, path):
        self.calls.append(("commit", path))
        self.checkpoints.pop(path)

    def rollback(self, path):
        self.calls.append(("rollback", path))
        self.profiles = self.checkpoints.pop(path)
