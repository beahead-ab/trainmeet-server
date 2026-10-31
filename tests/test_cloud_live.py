"""Klockan via Cloud: reläet (#205) och push (#206).

Cloud är låtsat (`FakeCloud`): den svarar som `POST /api/server-relay` och
`POST /api/server-push` gör, och sparar vad servern skickade. Boxarna är två
iPhone-TMBoxar som registrerat sig som vanligt, en på varje station. Ingen
riktig Cloud, ingen APNs och inget nät utanför maskinen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import unittest
from uuid import uuid4

import test_http_server
from tmbox_gateway import cloud_live
from tmbox_gateway.cloud_live import EVENT_FIELDS, RECIPIENT_FIELDS, RELAY_ROUTES, LiveError
from tmbox_gateway.identity import DeviceKind

WATCH_TOKEN = "ab" * 32
PHONE_TOKEN = "cd" * 32
LINK = "central-test-link"


class FakeCloud:
    def __init__(self):
        self.posts = []
        self.relay_answers = []
        self.push_answer = None
        self.unreachable = False

    def post(self, url, token, payload, *, timeout):
        self.posts.append({"url": url, "token": token, "payload": json.loads(json.dumps(payload))})
        if self.unreachable:
            raise LiveError("TrainMeet Cloud kunde inte nås")
        if url.endswith("/api/server-relay"):
            if self.relay_answers:
                return self.relay_answers.pop(0)
            return 200, {"protocol": 1, "relay_id": "relay-abc", "requests": [], "push": {"configured": True}}
        if self.push_answer is not None:
            return self.push_answer
        return 200, {"protocol": 1, "push": {"configured": True},
                     "results": [{"event_id": event["event_id"], "sent": len(event["recipients"]), "failed": 0, "invalid": []}
                                 for event in payload["events"]]}

    def pushed(self):
        return [event for post in self.posts if post["url"].endswith("/api/server-push") for event in post["payload"]["events"]]


class LiveChannelTests(unittest.TestCase):
    setUp_http = test_http_server.HTTPServerTests.setUp
    tearDown = test_http_server.HTTPServerTests.tearDown
    _json_request = test_http_server.HTTPServerTests._json_request
    _ensure_owner = test_http_server.HTTPServerTests._ensure_owner
    _public_client = test_http_server.HTTPServerTests._public_client
    _public_refused = test_http_server.HTTPServerTests._public_refused
    _linked_runtime = staticmethod(test_http_server.HTTPServerTests._linked_runtime)

    def setUp(self):
        self.setUp_http()
        self.cloud = FakeCloud()
        self.live = self.application.live
        self.live.transport = self.cloud
        self.now = [1000.0]
        self.live.wall = lambda: self.now[0]
        self.live.clock = lambda: self.now[0]
        self.sender = self._public_client(client="ios")
        self.receiver = self._public_client(client="ios")
        admin = self.application.local_admin()
        self.application.assign_device(admin, {"device_code": self.sender["device_code"], "station_id": "station-a"})
        self.application.assign_device(admin, {"device_code": self.receiver["device_code"], "station_id": "station-b"})
        self.log = []
        handler = logging.Handler()
        handler.emit = lambda record: self.log.append(record.getMessage() + (str(record.exc_info) if record.exc_info else ""))
        logger = logging.getLogger("tmbox_gateway")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

    def link(self):
        self.runtime_store.save_link_token(LINK)
        self.runtime_store.save_central_url("https://cloud.example/config")

    def turn_on(self):
        self.link()
        self._json_request("/v1/auth/login", {"email": "admin@example.se", "password": "test-password"})
        status = self._json_request("/v1/cloud/live", {"enabled": True})
        self._cookie = None
        return status

    def relay(self, method, path, body=None, token=None, request_id=None, expires=5000):
        request = {"id": request_id or "r-" + uuid4().hex, "method": method, "path": path,
                   "authorization": f"Bearer {token or self.receiver['access_token']}",
                   "body": json.dumps(body) if body is not None else None, "expires_in_ms": expires}
        self.cloud.relay_answers.append((200, {"protocol": 1, "relay_id": "relay-abc", "requests": [request],
                                               "push": {"configured": True}}))
        answers = self.live.exchange_once([], wait=0)
        return answers[0] if answers else None

    def press(self, box, key, **extra):
        frame = self._json_request("/v1/tmbox/terminal", token=box["access_token"])
        body = {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        result = self._json_request("/v1/tmbox/terminal", body, token=box["access_token"])
        self.assertEqual("accepted", result["status"], result)
        return result

    def register(self, box, device="watch", token=WATCH_TOKEN):
        return self._json_request("/v1/tmbox/push", {"device": device, "token": token, "environment": "production"},
                                  token=box["access_token"])

    def request_101(self):
        """CDA begär 101 till LEK, medan LEK:s app är stängd."""
        self.live.tick()  # Servern ser LEK:s box fast ingen app frågar.
        self.press(self.sender, "#", train_number="101")

    def assert_no_secret_logged(self, *extra):
        text = "\n".join(self.log)
        for secret in (LINK, self.sender["access_token"], self.receiver["access_token"], WATCH_TOKEN, PHONE_TOKEN, *extra):
            self.assertNotIn(secret, text)


class OffByDefaultTests(LiveChannelTests):
    def test_nothing_happens_until_an_administrator_turns_it_on(self):
        self.link()
        self.assertFalse(self.live.enabled())
        status = self._json_request("/v1/tmbox/watch", token=self.receiver["access_token"])
        self.assertEqual({"state": "off", "url": None}, status["relay"])
        self.assertEqual("off", status["push"]["state"])
        self.assertEqual([], self.live.exchange_once([]))
        self.register(self.receiver)
        self.request_101()
        self.assertEqual(0, self.live.flush_push())
        self.assertEqual([], self.cloud.posts, "ingenting går till Cloud när kanalen är av")

    def test_only_an_administrator_turns_it_on_and_only_when_linked(self):
        self._public_refused("/v1/cloud/live", {"enabled": True}, token=self.receiver["access_token"])
        self._public_refused("/v1/cloud/live", token=self.receiver["access_token"])
        self._json_request("/v1/auth/login", {"email": "admin@example.se", "password": "test-password"})
        self._public_refused("/v1/cloud/live", {"enabled": True}, status=400, headers={"Cookie": self._cookie})
        self.link()
        status = self._json_request("/v1/cloud/live", {"enabled": True})
        self.assertEqual((True, "connecting", "unknown"), (status["enabled"], status["relay"]["state"], status["push"]["state"]))
        status = self._json_request("/v1/cloud/live", {"enabled": False})
        self.assertEqual("off", status["relay"]["state"])


class RelayTests(LiveChannelTests):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_the_watch_reads_its_frame_through_cloud_with_the_box_key(self):
        answer = self.relay("GET", "/v1/tmbox/terminal")
        self.assertEqual(200, answer["status"])
        self.assertEqual("LEK", json.loads(answer["body"])["station_code"])
        exchange = self.cloud.posts[-1]
        self.assertEqual(("https://cloud.example/api/server-relay", LINK), (exchange["url"], exchange["token"]))
        self.assertEqual({"protocol", "wait", "responses"}, set(exchange["payload"]))
        status = self._json_request("/v1/tmbox/watch", token=self.receiver["access_token"])
        self.assertEqual({"state": "connected", "url": "https://cloud.example/relay/relay-abc"}, status["relay"])
        self.assertEqual("on", status["push"]["state"])
        self.assert_no_secret_logged()

    def test_every_tmbox_route_answers_and_nothing_else_runs(self):
        for method, path in sorted(RELAY_ROUTES):
            with self.subTest(route=(method, path)):
                answer = self.relay(method, path, {} if method == "POST" else None)
                self.assertNotEqual(404, answer["status"])
        for method, path in [("POST", "/v1/clock"), ("GET", "/v1/admin/users"), ("POST", "/v1/server/factory-reset"),
                             ("POST", "/v1/cloud/live"), ("GET", "/v1/server-context"), ("POST", "/v1/cloud/changes"),
                             ("POST", "/v1/browser-clients"), ("GET", "/v1/tmbox/terminal?x=1")]:
            with self.subTest(route=(method, path)):
                answer = self.relay(method, path, {} if method == "POST" else None)
                self.assertEqual((404, "not_found"), (answer["status"], json.loads(answer["body"])["error"]))

    def test_the_box_key_is_checked_on_the_pi_and_admins_are_not_let_through(self):
        self.assertEqual(401, self.relay("GET", "/v1/clock", token="fel-nyckel")["status"])
        no_key = self.relay("GET", "/v1/clock")
        self.assertEqual(200, no_key["status"])
        self.cloud.relay_answers.append((200, {"protocol": 1, "relay_id": "relay-abc", "push": {"configured": True},
                                               "requests": [{"id": "r-x", "method": "GET", "path": "/v1/clock",
                                                             "authorization": None, "body": None, "expires_in_ms": 5000}]}))
        self.assertEqual(401, self.live.exchange_once([], wait=0)[0]["status"])
        admin = self.application.pairing.pair(pairing_code=self.pairing_code, client_id="admin-mac",
                                              display_name="Admin-Mac", kind=DeviceKind.WEB_ADMIN)
        refused = self.relay("GET", "/v1/tmbox/terminal", token=admin.access_token)
        self.assertEqual((403, "box_required"), (refused["status"], json.loads(refused["body"])["error"]))
        removed = self._public_client(client="ios")
        self.identities.remove_discovered_device(removed["client_id"])
        self.assertEqual(401, self.relay("GET", "/v1/tmbox/terminal", token=removed["access_token"])["status"])

    def test_a_request_runs_once_and_never_after_the_watch_gave_up(self):
        first = self.relay("GET", "/v1/clock", request_id="r-same")
        self.assertEqual(200, first["status"])
        self.assertIsNone(self.relay("GET", "/v1/clock", request_id="r-same"), "samma id körs aldrig två gånger")
        self.assertIsNone(self.relay("GET", "/v1/clock", expires=0), "klockan har redan gett upp")

    def test_a_press_through_cloud_follows_the_same_rules_and_never_acts_twice(self):
        frame = json.loads(self.relay("GET", "/v1/tmbox/terminal", token=self.sender["access_token"])["body"])
        body = {"command_id": "c-relay-1", "view_token": frame["view_token"], "key": "#", "train_number": "101",
                "entry_context": frame["entry"]["context"]}
        answer = json.loads(self.relay("POST", "/v1/tmbox/terminal", body, token=self.sender["access_token"])["body"])
        self.assertEqual("accepted", answer["status"])
        self.assertEqual(1, len(self.application.station_service.open_cases(None)))
        again = json.loads(self.relay("POST", "/v1/tmbox/terminal", body, token=self.sender["access_token"])["body"])
        self.assertEqual("duplicate", again["status"])
        stale = json.loads(self.relay("POST", "/v1/tmbox/terminal", {**body, "command_id": "c-2", "view_token": "gammal"},
                                      token=self.sender["access_token"])["body"])
        self.assertEqual("rejected", stale["status"])
        self.assertEqual(1, len(self.application.station_service.open_cases(None)))

    def test_cloud_saying_no_is_shown_and_not_retried_in_a_hurry(self):
        self.cloud.relay_answers.append((401, {"error": "Serverkopplingen är ogiltig", "code": "link_invalid"}))
        with self.assertRaises(LiveError):
            self.live.exchange_once([])
        self.assertEqual("link_rejected", self.live.status()["relay"]["state"])
        # En äldre Cloud känner inte vägen och ber om inloggning.
        self.cloud.relay_answers.append((401, {"error": "Logga in för att fortsätta"}))
        with self.assertRaises(LiveError) as old:
            self.live.exchange_once([])
        self.assertEqual(("cloud_disabled", "relay_disabled"), (self.live.status()["relay"]["state"], old.exception.code))
        self.cloud.relay_answers.append((503, {"error": "Reläet är inte påslaget", "code": "relay_disabled",
                                               "push": {"configured": False}}))
        with self.assertRaises(LiveError) as off:
            self.live.exchange_once([])
        self.assertEqual("relay_disabled", off.exception.code)
        status = self._json_request("/v1/tmbox/watch", token=self.receiver["access_token"])
        self.assertEqual(({"state": "cloud_disabled", "url": None}, "cloud_not_configured"), (status["relay"], status["push"]["state"]))

    def test_an_unexpected_error_is_an_answer_without_a_key_in_the_log(self):
        original = self.application.clock_status
        self.application.clock_status = lambda client: (_ for _ in ()).throw(RuntimeError("trasig"))
        try:
            answer = self.relay("GET", "/v1/clock")
        finally:
            self.application.clock_status = original
        self.assertEqual((500, "internal_error"), (answer["status"], json.loads(answer["body"])["error"]))
        self.assert_no_secret_logged()


class PushTests(LiveChannelTests):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_registration_is_the_boxs_own_and_checked(self):
        status = self.register(self.receiver)
        self.assertEqual({"phone": False, "watch": True}, status["push"]["registered"])
        for bad in ({"device": "ipad", "token": WATCH_TOKEN}, {"device": "watch", "token": "not-hex"},
                    {"device": "watch", "token": WATCH_TOKEN, "environment": "dev"},
                    {"device": "watch", "token": WATCH_TOKEN, "station_id": "station-a"}):
            with self.subTest(bad=bad):
                self._public_refused("/v1/tmbox/push", bad, token=self.receiver["access_token"], status=400)
        self._public_refused("/v1/tmbox/push", {"device": "watch", "token": WATCH_TOKEN}, status=401)
        # Samma token på en annan box gäller bara där.
        self.register(self.sender)
        self.assertEqual({"phone": False, "watch": False},
                         self._json_request("/v1/tmbox/watch", token=self.receiver["access_token"])["push"]["registered"])
        removed = self._json_request("/v1/tmbox/push", {"device": "watch", "token": None}, token=self.sender["access_token"])
        self.assertEqual(False, removed["registered"])
        self.assertEqual({}, self.live._registrations())

    def test_a_request_reaches_the_closed_app_with_only_what_the_notice_needs(self):
        self.register(self.receiver, "watch", WATCH_TOKEN)
        self.register(self.receiver, "phone", PHONE_TOKEN)
        self.request_101()
        self.assertEqual(1, self.live.flush_push())
        [event] = self.cloud.pushed()
        self.assertLessEqual(set(event), EVENT_FIELDS)
        self.assertEqual(("incoming_request", "101", "CDA", "LEK", "sv"),
                         (event["kind"], event["train_number"], event["from_station"], event["to_station"], event["language"]))
        case = self.application.station_service.open_cases(None)[0]
        self.assertEqual(case["clearance_id"], event["request_id"])
        self.assertGreaterEqual(len(event["proof"]), 22)
        self.assertEqual([{"device": "phone", "token": PHONE_TOKEN, "environment": "production"},
                          {"device": "watch", "token": WATCH_TOKEN, "environment": "production"}], event["recipients"])
        self.assertTrue(all(set(recipient) == RECIPIENT_FIELDS for recipient in event["recipients"]))
        post = [post for post in self.cloud.posts if post["url"].endswith("/api/server-push")][0]
        self.assertEqual(("https://cloud.example/api/server-push", LINK, {"protocol", "events"}),
                         (post["url"], post["token"], set(post["payload"])))
        self.assert_no_secret_logged(event["proof"])

    def test_clear_from_the_notice_clears_only_that_request_and_only_while_it_waits(self):
        self.register(self.receiver)
        self.register(self.sender, "phone", PHONE_TOKEN)
        self.request_101()
        self.live.flush_push()
        [event] = self.cloud.pushed()
        answer = {"request_id": event["request_id"], "proof": event["proof"], "answer": "approve"}
        result = self._json_request("/v1/tmbox/answer", answer, token=self.receiver["access_token"])
        self.assertEqual(("accepted", "approved"), (result["status"], result["decision"]))
        clearance = self.operations_store.clearance(event["request_id"])
        self.assertEqual("approved", clearance["status"])
        # Ett andra tryck, från klockan eller telefonen: samma besked, ingen ny åtgärd.
        again = self._json_request("/v1/tmbox/answer", {**answer, "answer": "deny"}, token=self.receiver["access_token"])
        self.assertEqual(("duplicate", "approved"), (again["status"], again["decision"]))
        self.assertEqual("approved", self.operations_store.clearance(event["request_id"])["status"])
        # CDA hör svaret, som boxen gör, inom en sekund.
        self.now[0] += 1
        self.live.tick()
        self.live.flush_push()
        self.assertEqual("request_approved", self.cloud.pushed()[-1]["kind"])
        self.assertNotIn("proof", self.cloud.pushed()[-1])

    def test_an_answer_to_a_request_already_decided_is_refused(self):
        self.register(self.receiver)
        self.request_101()
        self.live.flush_push()
        [event] = self.cloud.pushed()
        self.press(self.receiver, "A")
        self.press(self.receiver, "*")
        self.press(self.receiver, "#")  # LEK nekar på boxen innan notisen besvaras.
        self.assertEqual("rejected", self.operations_store.clearance(event["request_id"])["status"])
        self._public_refused("/v1/tmbox/answer", {"request_id": event["request_id"], "proof": event["proof"], "answer": "approve"},
                             token=self.receiver["access_token"], status=409)
        self.assertEqual("rejected", self.operations_store.clearance(event["request_id"])["status"])

    def test_the_proof_belongs_to_one_box_one_request_and_a_few_minutes(self):
        self.register(self.receiver)
        self.request_101()
        self.live.flush_push()
        [event] = self.cloud.pushed()
        answer = {"request_id": event["request_id"], "proof": event["proof"], "answer": "approve"}
        self._public_refused("/v1/tmbox/answer", answer, token=self.sender["access_token"], status=403)
        self._public_refused("/v1/tmbox/answer", {**answer, "request_id": "annan"}, token=self.receiver["access_token"], status=403)
        self._public_refused("/v1/tmbox/answer", {**answer, "proof": "x" * 32}, token=self.receiver["access_token"], status=410)
        self._public_refused("/v1/tmbox/answer", {**answer, "answer": "maybe"}, token=self.receiver["access_token"], status=400)
        self.now[0] += cloud_live.PROOF_SECONDS + 1
        self._public_refused("/v1/tmbox/answer", answer, token=self.receiver["access_token"], status=410)
        self.assertEqual("waiting", self.operations_store.clearance(event["request_id"])["status"])

    def test_a_box_moved_to_another_station_cannot_answer_for_the_old_one(self):
        self.register(self.receiver)
        self.request_101()
        self.live.flush_push()
        [event] = self.cloud.pushed()
        self.application.assign_device(self.application.local_admin(),
                                       {"device_code": self.receiver["device_code"], "station_id": "station-a"})
        self._public_refused("/v1/tmbox/answer", {"request_id": event["request_id"], "proof": event["proof"], "answer": "approve"},
                             token=self.receiver["access_token"], status=409)

    def test_the_answer_can_come_through_the_relay(self):
        self.register(self.receiver)
        self.request_101()
        self.live.flush_push()
        [event] = self.cloud.pushed()
        answer = self.relay("POST", "/v1/tmbox/answer", {"request_id": event["request_id"], "proof": event["proof"], "answer": "deny"})
        self.assertEqual((200, "denied"), (answer["status"], json.loads(answer["body"])["decision"]))
        self.assertEqual("rejected", self.operations_store.clearance(event["request_id"])["status"])

    def test_no_storm_of_old_notices_after_an_outage(self):
        self.register(self.receiver)
        self.request_101()
        self.cloud.unreachable = True
        self.assertEqual(0, self.live.flush_push())
        self.cloud.unreachable = False
        self.assertEqual(1, self.live.flush_push(), "ett nytt försök inom minuten")
        self.cloud.posts.clear()
        self.press(self.sender, "*")  # Återta 101 ...
        self.press(self.sender, "#")
        self.press(self.sender, "#", train_number="101")  # ... och begär igen.
        self.now[0] += cloud_live.EVENT_MAX_AGE_SECONDS + 1
        self.assertEqual(0, self.live.flush_push())
        self.assertEqual([], self.cloud.pushed())

    def test_cloud_without_apns_and_tokens_apple_forgot(self):
        self.register(self.receiver)
        self.request_101()
        self.cloud.push_answer = (503, {"error": "Push är inte konfigurerat", "code": "push_not_configured",
                                        "push": {"configured": False}})
        self.live.flush_push()
        self.assertEqual("cloud_not_configured", self.live.status()["push"]["state"])
        self.cloud.push_answer = None
        self.cloud.push_answer = (200, {"protocol": 1, "push": {"configured": True}, "results": [
            {"event_id": "e", "sent": 0, "failed": 1, "invalid": [hashlib.sha256(WATCH_TOKEN.encode()).hexdigest()[:16]]}]})
        self.press(self.sender, "*")
        self.press(self.sender, "#")
        self.press(self.sender, "#", train_number="101")
        self.live.flush_push()
        self.assertEqual({}, self.live._registrations(), "en token Apple glömt används inte igen")

    def test_a_removed_box_gets_no_notice(self):
        self.register(self.receiver)
        self.request_101()
        self.identities.remove_discovered_device(self.receiver["client_id"])
        self.live.flush_push()
        self.assertEqual([], self.cloud.pushed())


if __name__ == "__main__":
    unittest.main()
