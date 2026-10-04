"""E-post till serverns användare, via TrainMeet Cloud.

Servern har ingen egen e-post. Är den kopplad till Cloud kan en inbjudan och
en kod för nytt lösenord skickas till kontots adress; Cloud har avsändaren och
mallarna. Koden visas ändå alltid för ägaren, och utan koppling fungerar allt
som förut. Inget riktigt brev skickas här: Cloud är en låtsad mottagare.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tmbox_gateway.central_sync import CentralSyncError
from tmbox_gateway.cloud_mail import send_server_mail, server_mail_url
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPAPIError,
    HTTPServerConfig,
    TrainMeetHTTPApplication,
    TrainMeetHTTPServer,
)
from tmbox_gateway.identity import (
    ADMIN_RESET_TTL,
    AdminAccessError,
    DeviceKind,
    IdentityStore,
    PairedClient,
    PairingService,
)
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore
from session_fixture import sample_session


class IdentityMailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        invited = self.identities.invite_admin_user("Benny", " Benny@Example.SE ", "admin")
        self.identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "bennys-losenord")
        self.benny = str(invited["user_id"])

    def user(self, name: str) -> dict:
        return next(u for u in self.identities.list_admin_users() if u["display_name"] == name)

    def test_an_address_is_required_and_stored_in_lower_case(self) -> None:
        """Adressen är kontot. Den kan bytas men inte tas bort."""

        self.assertEqual("benny@example.se", self.user("Benny")["email"])
        self.assertEqual("casper@example.se", self.user("Casper")["email"])
        with self.assertRaises(AdminAccessError):
            self.identities.set_admin_user_email(self.benny, "")
        with self.assertRaises(AdminAccessError):
            self.identities.invite_admin_user("Lars", "", "admin")
        self.assertEqual("benny@example.se", self.user("Benny")["email"])

    def test_two_accounts_cannot_share_an_address(self) -> None:
        with self.assertRaises(AdminAccessError):
            self.identities.invite_admin_user("Benny igen", "BENNY@example.se", "admin")
        with self.assertRaises(AdminAccessError):
            self.identities.set_admin_user_email(self.benny, "Casper@Example.se")
        self.identities.set_admin_user_email(self.benny, "benny@example.se")

    def test_something_that_is_not_an_address_is_refused(self) -> None:
        for value in ("benny", "benny@", "@example.se", "benny@example", "a b@example.se", "x" * 250 + "@example.se"):
            with self.subTest(value=value):
                with self.assertRaises(AdminAccessError):
                    self.identities.set_admin_user_email(self.benny, value)
                with self.assertRaises(AdminAccessError):
                    self.identities.invite_admin_user(f"U{len(value)}", value, "admin")

    def test_no_code_for_an_unknown_account_or_one_without_an_address(self) -> None:
        self.assertIsNone(self.identities.issue_admin_password_reset("finns-inte@example.se"))
        self.assertIsNone(self.identities.issue_admin_password_reset("Casper"), "ett namn är inte en adress")
        self.assertIsNone(self.identities.issue_admin_password_reset("<script>"))

    def test_a_code_sets_a_new_password_once_and_logs_out_old_sessions(self) -> None:
        session = self.identities.create_admin_session("benny@example.se", "bennys-losenord")
        user, code = self.identities.issue_admin_password_reset("BENNY@example.se")
        self.assertEqual("benny@example.se", user["email"])
        # Det gamla lösenordet gäller tills koden lösts in.
        self.assertIsNotNone(self.identities.create_admin_session("benny@example.se", "bennys-losenord"))
        self.identities.redeem_admin_setup("benny@example.se", code.lower().replace("-", " "), "ett-nytt-losenord")
        self.assertIsNone(self.identities.create_admin_session("benny@example.se", "bennys-losenord"))
        self.assertIsNotNone(self.identities.create_admin_session("benny@example.se", "ett-nytt-losenord"))
        self.assertIsNone(self.identities.admin_session_user(str(session)))
        with self.assertRaisesRegex(AdminAccessError, "gäller inte"):
            self.identities.redeem_admin_setup("benny@example.se", code, "ett-tredje-losenord")

    def test_a_code_expires_after_thirty_minutes(self) -> None:
        now = datetime.now(timezone.utc)
        _, code = self.identities.issue_admin_password_reset("benny@example.se", now=now)
        with self.assertRaisesRegex(AdminAccessError, "Begär en ny"):
            self.identities.redeem_admin_setup("benny@example.se", code, "ett-nytt-losenord", now=now + ADMIN_RESET_TTL + timedelta(seconds=1))
        self.identities.redeem_admin_setup("benny@example.se", code, "ett-nytt-losenord", now=now + ADMIN_RESET_TTL - timedelta(seconds=1))
        self.assertEqual(timedelta(minutes=30), ADMIN_RESET_TTL)

    def test_a_new_code_replaces_the_previous_one(self) -> None:
        _, first = self.identities.issue_admin_password_reset("benny@example.se")
        _, second = self.identities.issue_admin_password_reset("benny@example.se")
        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("benny@example.se", first, "ett-nytt-losenord")
        self.identities.redeem_admin_setup("benny@example.se", second, "ett-nytt-losenord")

    def test_a_code_belongs_to_its_own_account(self) -> None:
        other = self.identities.invite_admin_user("Lars", "lars@example.se", "admin")
        self.identities.redeem_admin_setup("lars@example.se", str(other["setup_code"]), "lars-losenord")
        _, code = self.identities.issue_admin_password_reset("benny@example.se")
        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("lars@example.se", code, "ett-nytt-losenord")

    def test_the_code_is_not_stored_as_written(self) -> None:
        _, code = self.identities.issue_admin_password_reset("benny@example.se")
        stored = self.identities._connection.execute(
            "SELECT reset_code_digest FROM admin_users WHERE email = 'benny@example.se'"
        ).fetchone()[0]
        self.assertNotIn(code.encode(), bytes(stored))
        self.assertNotIn(code.replace("-", "").encode(), bytes(stored))

    def test_an_invitation_code_still_works_beside_a_password_code(self) -> None:
        invited = self.identities.invite_admin_user("Lars", "lars@example.se", "admin")
        self.identities.issue_admin_password_reset("lars@example.se")
        self.identities.redeem_admin_setup("lars@example.se", str(invited["setup_code"]), "lars-losenord")
        self.assertIsNotNone(self.identities.create_admin_session("lars@example.se", "lars-losenord"))


class FakeCloud:
    """Tar emot det servern skulle skicka till Cloud."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []
        self.error: str | None = None

    def __call__(self, token: str, url: str, payload: dict) -> None:
        if self.error:
            raise CentralSyncError(self.error)
        self.sent.append((token, url, payload))


class ApplicationMailTests(unittest.TestCase):
    public_origin = ""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        self.runtime = SQLiteRuntimeStore(Path(self._dir.name) / "runtime.db")
        self.addCleanup(self.runtime.close)
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.app = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True, public_client_origin=self.public_origin,
                             central_runtime_url="https://cloud.example/config"),
            runtime_store=self.runtime,
        )
        self.cloud = FakeCloud()
        self.app.server_mailer = self.cloud
        self.app.run_in_background = lambda job: job()

    def link(self) -> None:
        self.runtime.save_link_token("kopplingsnyckel")

    def client(self, name: str) -> PairedClient:
        user = next(u for u in self.identities.list_admin_users() if u["email"] == f"{name}@example.se")
        return PairedClient(client_id="local-web-admin", display_name=str(user["display_name"]), kind=DeviceKind.WEB_ADMIN,
                            panel_ids=(), admin_user_id=str(user["user_id"]), admin_role=str(user["role"]))

    def invite(self, **extra) -> dict:
        return self.app.create_admin_user(self.client("casper"), {"display_name": "Benny", "email": "benny@example.se",
                                                                  "role": "admin", **extra})


class InvitationMailTests(ApplicationMailTests):
    def test_a_linked_server_sends_the_invitation_through_cloud(self) -> None:
        self.link()
        result = self.invite(email="benny@example.se", server_url="http://trainmeet.local:8787", language="en")
        self.assertEqual({"status": "sent", "to": "benny@example.se"}, result["mail"])
        [(token, url, payload)] = self.cloud.sent
        self.assertEqual(("kopplingsnyckel", "https://cloud.example/config"), (token, url))
        # Inget användarnamn: kontot är adressen, och Cloud skriver den i brevet.
        self.assertEqual({"kind": "invite", "to": "benny@example.se",
                          "code": result["user"]["setup_code"], "server_url": "http://trainmeet.local:8787",
                          "language": "en"}, payload)
        self.assertTrue(result["user"]["setup_code"], "the owner still sees the code")

    def test_a_new_code_is_sent_again(self) -> None:
        self.link()
        user = self.invite(email="benny@example.se")["user"]
        result = self.app.reissue_admin_setup(self.client("casper"), {"user_id": user["user_id"]})
        self.assertEqual("sent", result["mail"]["status"])
        self.assertEqual(result["user"]["setup_code"], self.cloud.sent[-1][2]["code"])

    def test_without_a_link_or_an_address_the_code_is_handed_over_as_before(self) -> None:
        self.assertEqual({"status": "not_linked"}, self.invite(email="benny@example.se")["mail"])
        self.link()
        other = self.app.create_admin_user(self.client("casper"), {"display_name": "Lars", "email": "lars@example.se"})
        # Ett konto från före version 3 kan sakna adress. Då lämnas koden över.
        self.identities._connection.execute("UPDATE admin_users SET email = NULL WHERE user_id = ?", (other["user"]["user_id"],))
        self.cloud.sent.clear()
        again = self.app.reissue_admin_setup(self.client("casper"), {"user_id": other["user"]["user_id"]})
        self.assertEqual({"status": "no_email"}, again["mail"])
        self.assertTrue(again["user"]["setup_code"])
        self.assertEqual([], self.cloud.sent)

    def test_an_invitation_needs_a_name_and_an_address(self) -> None:
        for body in ({"email": "lars@example.se"}, {"display_name": "Lars"}, {"username": "lars"}):
            with self.subTest(body=body), self.assertRaises(HTTPAPIError) as raised:
                self.app.create_admin_user(self.client("casper"), body)
            self.assertEqual(400, raised.exception.status)

    def test_a_failed_mail_still_creates_the_invitation(self) -> None:
        self.link()
        self.cloud.error = "För många brev. Vänta en stund och försök igen."
        result = self.invite(email="benny@example.se")
        self.assertEqual({"status": "failed", "message": "För många brev. Vänta en stund och försök igen."}, result["mail"])
        self.assertTrue(result["user"]["setup_code"])
        self.assertIn("benny@example.se", [u["email"] for u in self.identities.list_admin_users()])

    def test_an_address_the_owner_did_not_mean_is_left_out(self) -> None:
        self.link()
        for url in ("javascript:alert(1)", "https://cloud.trainmeet.app@annan.example", "http://x/?q=1", "inte en adress"):
            with self.subTest(url=url):
                self.app.reissue_admin_setup(self.client("casper"), {"user_id": self.invite_once()["user_id"], "server_url": url})
                self.assertEqual("", self.cloud.sent[-1][2]["server_url"])

    def invite_once(self) -> dict:
        existing = next((u for u in self.identities.list_admin_users() if u["email"] == "benny@example.se"), None)
        return existing or self.invite(email="benny@example.se")["user"]


class InvitationMailBehindAPublicAddressTests(ApplicationMailTests):
    public_origin = "https://server.trainmeet.app"

    def test_the_configured_address_wins(self) -> None:
        self.link()
        self.invite(email="benny@example.se", server_url="http://192.168.1.5:8787")
        self.assertEqual("https://server.trainmeet.app", self.cloud.sent[0][2]["server_url"])

    def test_a_password_code_points_to_the_configured_address(self) -> None:
        self.link()
        self.invite(email="benny@example.se")
        self.app.request_password_reset({"email": "benny@example.se", "server_url": "https://annan.example"}, "10.0.0.9")
        self.assertEqual("https://server.trainmeet.app", self.cloud.sent[-1][2]["server_url"])


class PasswordResetTests(ApplicationMailTests):
    def setUp(self) -> None:
        super().setUp()
        self.link()
        user = self.invite(email="benny@example.se")["user"]
        self.identities.redeem_admin_setup("benny@example.se", user["setup_code"], "bennys-losenord")
        self.cloud.sent.clear()

    def test_the_code_reaches_the_accounts_own_address_and_works(self) -> None:
        answer = self.app.request_password_reset({"email": " Benny@Example.se ", "language": "en"}, "10.0.0.9")
        self.assertEqual({"email_available": True}, answer)
        [(_, _, payload)] = self.cloud.sent
        self.assertEqual(("password_reset", "benny@example.se", "en"),
                         (payload["kind"], payload["to"], payload["language"]))
        self.assertNotIn("username", payload)
        self.identities.redeem_admin_setup("benny@example.se", payload["code"], "ett-nytt-losenord")
        self.assertIsNotNone(self.identities.create_admin_session("benny@example.se", "ett-nytt-losenord"))

    def test_the_answer_is_the_same_whether_or_not_the_account_exists(self) -> None:
        answers = [self.app.request_password_reset({"email": email}, f"10.0.0.{n}")
                   for n, email in enumerate(("benny@example.se", "finns-inte@example.se", "Benny", ""))]
        self.assertEqual([{"email_available": True}] * 4, answers)
        self.assertEqual(["benny@example.se"], [payload["to"] for _, _, payload in self.cloud.sent])

    def test_the_address_in_the_request_is_never_used(self) -> None:
        self.app.request_password_reset({"email": "benny@example.se", "server_url": "https://annan.example"}, "10.0.0.9")
        self.assertEqual("", self.cloud.sent[0][2]["server_url"])

    def test_a_few_codes_per_account_and_hour(self) -> None:
        for n in range(self.app.RESET_PER_ACCOUNT + 2):
            self.app.request_password_reset({"email": "Benny@example.se" if n % 2 else "benny@example.se"}, f"10.0.1.{n}")
        self.assertEqual(self.app.RESET_PER_ACCOUNT, len(self.cloud.sent))

    def test_a_limited_number_of_requests_per_address(self) -> None:
        for n in range(self.app.RESET_PER_ADDRESS + 2):
            self.app.request_password_reset({"email": "benny@example.se" if n == self.app.RESET_PER_ADDRESS else f"x{n}@example.se"}, "10.0.0.9")
        self.assertEqual([], self.cloud.sent)

    def test_without_a_link_the_page_is_told_there_is_no_email(self) -> None:
        self.runtime._connection.execute("DELETE FROM runtime_settings WHERE key = 'central_link_token'")
        self.assertEqual({"email_available": False}, self.app.request_password_reset({"email": "benny@example.se"}, "10.0.0.9"))
        self.assertEqual([], self.cloud.sent)

    def test_the_work_happens_after_the_answer(self) -> None:
        jobs = []
        self.app.run_in_background = jobs.append
        self.app.request_password_reset({"email": "benny@example.se"}, "10.0.0.9")
        self.assertEqual([], self.cloud.sent)
        self.assertIsNone(
            self.identities._connection.execute("SELECT reset_code_digest FROM admin_users WHERE email='benny@example.se'").fetchone()[0],
            "the account is not even looked up before the answer",
        )
        jobs[0]()
        self.assertEqual(1, len(self.cloud.sent))


class EmailEditingTests(ApplicationMailTests):
    def setUp(self) -> None:
        super().setUp()
        user = self.invite()["user"]
        self.identities.redeem_admin_setup("benny@example.se", user["setup_code"], "bennys-losenord")
        self.benny = user["user_id"]

    def test_everyone_sets_their_own_address_and_the_owner_any(self) -> None:
        self.app.update_admin_user(self.client("benny"), {"user_id": self.benny, "email": "benny.b@example.se"})
        self.assertEqual("benny.b@example.se", self.identities.admin_user(self.benny)["email"])
        casper = self.client("casper").admin_user_id
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.update_admin_user(self.client("benny.b"), {"user_id": casper, "email": "benny@example.se"})
        self.assertEqual(403, raised.exception.status)
        self.app.update_admin_user(self.client("casper"), {"user_id": self.benny, "email": "benny@example.se"})
        for refused in ("", "inte en adress", "casper@example.se"):
            with self.subTest(email=refused), self.assertRaises(HTTPAPIError) as raised:
                self.app.update_admin_user(self.client("casper"), {"user_id": self.benny, "email": refused})
            self.assertEqual(400, raised.exception.status)
        self.assertEqual("benny@example.se", self.identities.admin_user(self.benny)["email"])


class PasswordResetOverHTTPTests(ApplicationMailTests):
    force_external_auth = True

    def setUp(self) -> None:
        super().setUp()
        self.link()
        self.invite(email="benny@example.se")
        self.cloud.sent.clear()
        self.app.config = HTTPServerConfig(local_development=False, force_external_auth=True,
                                           central_runtime_url="https://cloud.example/config")
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_the_route_answers_without_a_login(self) -> None:
        request = Request(f"http://127.0.0.1:{self.server.server_port}/v1/admin/password-reset",
                          data=json.dumps({"email": "benny@example.se"}).encode(), method="POST",
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            self.assertEqual(202, response.status)
            self.assertEqual({"email_available": True}, json.loads(response.read()))
        self.assertEqual("benny@example.se", self.cloud.sent[0][2]["to"])


class CloudClientTests(unittest.TestCase):
    """send_server_mail mot en låtsad Cloud över riktig HTTP."""

    def setUp(self) -> None:
        received = self.received = []
        status = self.status = {"code": 202, "body": {"queued": True}}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers.get("Authorization"), body))
                data = json.dumps(status["body"]).encode()
                self.send_response(status["code"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config_url = f"http://127.0.0.1:{self.server.server_port}/config"

    def test_the_key_goes_in_the_header_and_the_letter_in_the_body(self) -> None:
        send_server_mail("nyckel", self.config_url, {"kind": "invite", "to": "a@example.se"})
        self.assertEqual([("/api/server-mail", "Bearer nyckel", {"kind": "invite", "to": "a@example.se"})], self.received)

    def test_clouds_own_words_reach_the_server(self) -> None:
        self.status.update(code=429, body={"error": "För många brev. Vänta en stund och försök igen."})
        with self.assertRaisesRegex(CentralSyncError, "För många brev"):
            send_server_mail("nyckel", self.config_url, {"kind": "invite"})

    def test_only_the_letter_leaves_the_server(self) -> None:
        send_server_mail("nyckel", self.config_url, {"kind": "invite", "to": "a@example.se", "meet": {"stations": []},
                                                    "history": [1, 2], "username": "a1b"})
        self.assertEqual({"kind": "invite", "to": "a@example.se"}, self.received[0][2])

    def test_a_silent_failure_is_named_as_mail(self) -> None:
        self.status.update(code=502, body=[])
        with self.assertRaisesRegex(CentralSyncError, "kunde inte skicka e-posten"):
            send_server_mail("nyckel", self.config_url, {"kind": "invite"})

    def test_no_key_no_request(self) -> None:
        with self.assertRaises(CentralSyncError):
            send_server_mail(" ", self.config_url, {})
        self.assertEqual([], self.received)

    def test_the_address_follows_the_configured_cloud(self) -> None:
        self.assertEqual("https://cloud.trainmeet.app/api/server-mail", server_mail_url("https://trainmeet.app/konfig"))
        self.assertEqual("http://h:8/api/server-mail", server_mail_url("http://h:8/config?x=1"))


if __name__ == "__main__":
    unittest.main()
