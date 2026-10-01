"""retire.py: provbänken på server.trainmeet.app flyttar in i Servern.

Den fristående tjänsten körde en fast commit och drev isär från Servern: gammal
sida och gammal knappsats. Skriptet pekar om proxyn och stänger tjänsten, och
får aldrig lämna värden halvvägs: vid varje fel före den verifierade
omkopplingen ska Caddyfile vara som förut och den gamla tjänsten fortfarande gå.
Provas här mot en falsk Caddyfile och falska kommandon; ingen riktig värd rörs.
"""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy/terminal16" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER_LAB = {"status": "ok", "service": "tmbox-lab", "served_by": "server", "version": "1.17.3"}
STANDALONE_LAB = {"status": "ok", "service": "tmbox-lab", "profile": "server-16x2-pilot"}
IDENTITY = {"server": {"status": "ok"}, "cloud": {"status": "ok"}, "server_start": "s", "cloud_start": "c"}


class RetireTheStandaloneLabTests(unittest.TestCase):
    def setUp(self):
        self.retire = load("retire")
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.retire.CADDY = root / "Caddyfile"
        self.retire.CLOUD_SITE = root / "cloud.caddy"
        self.retire.BACKUPS = root / "backups"
        self.before = "{\n    email ops@example.invalid\n}\n\n" + self.retire.NEW_BLOCK + "\n\nimport sites/*\n"
        self.retire.CADDY.write_text(self.before)
        self.retire.CLOUD_SITE.write_text("cloud.trainmeet.app { reverse_proxy 127.0.0.1:8791 }\n")
        self.commands = []
        self.failing = None
        self.public = dict(SERVER_LAB)
        self.server = dict(SERVER_LAB)
        self.identities = [IDENTITY, IDENTITY]

        def run(*args):
            self.commands.append(args)
            if self.failing and args[:2] == self.failing:
                raise RuntimeError("simulerat fel: " + " ".join(args))
            return ""

        self.retire.run = run
        self.retire.server_lab = lambda: self.server
        self.retire.public_lab = lambda: self.public
        self.retire.runtime_identity = lambda: self.identities.pop(0)

    def stopped(self):
        return ("systemctl", "disable", "--now", "trainmeet-tmbox-lab") in self.commands

    def test_the_constants_match_what_deploy_installed(self):
        deploy = load("deploy")
        self.assertEqual((deploy.OLD_BLOCK, deploy.NEW_BLOCK), (self.retire.OLD_BLOCK, self.retire.NEW_BLOCK))

    def test_switches_the_proxy_to_server_then_stops_the_service(self):
        self.retire.retire()
        after = self.retire.CADDY.read_text()
        self.assertEqual(self.before.replace(self.retire.NEW_BLOCK, self.retire.OLD_BLOCK), after)
        self.assertNotIn("8797", after)
        self.assertEqual([("caddy", "validate"), ("systemctl", "reload"), ("systemctl", "disable")],
                         [command[:2] for command in self.commands])
        backup = next(self.retire.BACKUPS.iterdir())
        self.assertEqual(self.before, (backup / "Caddyfile").read_text())
        self.assertTrue((backup / "retired.json").is_file())

    def test_nothing_changes_until_server_serves_the_lab_itself(self):
        for answer in (STANDALONE_LAB, {"status": "ok"}):
            with self.subTest(answer=answer):
                self.server = answer
                with self.assertRaisesRegex(RuntimeError, "1.17.3"):
                    self.retire.retire()
                self.assertEqual(self.before, self.retire.CADDY.read_text())
                self.assertEqual([], self.commands)

    def test_an_invalid_proxy_file_is_never_loaded(self):
        self.failing = ("caddy", "validate")
        with self.assertRaises(RuntimeError):
            self.retire.retire()
        self.assertEqual(self.before, self.retire.CADDY.read_text())
        self.assertFalse(self.stopped())
        self.assertNotIn(("systemctl", "reload", "caddy"), self.commands)

    def test_a_route_that_still_reaches_the_old_service_is_rolled_back(self):
        self.public = dict(STANDALONE_LAB)
        with self.assertRaisesRegex(RuntimeError, "HTTPS route"):
            self.retire.retire()
        self.assertEqual(self.before, self.retire.CADDY.read_text())
        self.assertEqual(2, self.commands.count(("systemctl", "reload", "caddy")), "byt och byt tillbaka")
        self.assertFalse(self.stopped(), "den gamla tjänsten ska fortsätta svara")

    def test_a_restarted_server_or_cloud_is_rolled_back(self):
        self.identities = [IDENTITY, {**IDENTITY, "server_start": "omstartad"}]
        with self.assertRaisesRegex(RuntimeError, "baseline"):
            self.retire.retire()
        self.assertEqual(self.before, self.retire.CADDY.read_text())
        self.assertFalse(self.stopped())

    def test_an_edit_while_validating_is_kept_and_nothing_is_loaded(self):
        def someone_edits_during_validate(*args):
            self.commands.append(args)
            if args[:2] == ("caddy", "validate"):
                self.retire.CADDY.write_text("# någon annans ändring\n")
            return ""

        self.retire.run = someone_edits_during_validate
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            self.retire.retire()
        self.assertEqual("# någon annans ändring\n", self.retire.CADDY.read_text())
        self.assertNotIn(("systemctl", "reload", "caddy"), self.commands)
        self.assertFalse(self.stopped())

    def test_a_concurrent_edit_is_never_overwritten(self):
        def reload_then_someone_edits(*args):
            self.commands.append(args)
            if args[:2] == ("systemctl", "reload") and len(self.commands) == 2:
                self.retire.CADDY.write_text("# någon annans ändring\n")
            return ""

        self.retire.run = reload_then_someone_edits
        self.public = dict(STANDALONE_LAB)
        with self.assertRaises(RuntimeError):
            self.retire.retire()
        self.assertEqual("# någon annans ändring\n", self.retire.CADDY.read_text())

    def test_an_unknown_proxy_file_is_left_alone(self):
        self.retire.CADDY.write_text("server.trainmeet.app {\n    reverse_proxy 127.0.0.1:9999\n}\n")
        with self.assertRaisesRegex(RuntimeError, "inspect"):
            self.retire.retire()
        self.assertEqual([], self.commands)

    def test_running_it_again_only_makes_sure_the_service_is_off(self):
        self.retire.retire()
        self.commands.clear()
        self.retire.retire()
        self.assertEqual([("systemctl", "disable", "--now", "trainmeet-tmbox-lab")], self.commands)


if __name__ == "__main__":
    unittest.main()
