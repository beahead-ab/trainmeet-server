"""Live US read endpoints never expose a different meet or operating session."""
from copy import deepcopy
from http import HTTPStatus

from test_pending_revisions import CloudDeliveryFixture, dispatch_request
from test_us_cloud import cloud_package
from tmbox_gateway.central_sync import CentralRuntimeDownload
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.us import USStore


class USLiveReadScopeTests(CloudDeliveryFixture):
    def setUp(self):
        super().setUp()
        self.us = USStore(self.runtime.path)
        self.addCleanup(self.us.close)
        self.application.us_store = self.us
        self.package = cloud_package()
        self.application.runtime_fetcher = lambda code, url: CentralRuntimeDownload(deepcopy(self.package), "us-link")
        self.application.sync_runtime(self.client, {"sync_code": "123456", "confirm_meet_change": True})
        self.actor = self.application.us_access(self.client)[1]

    def get(self, path, client=None):
        return dispatch_request(self.application, client or self.client, path, method="GET")

    def start(self, command_id):
        return self.application.us_command(self.client, {"action": "create_session", "command_id": command_id,
            "publication_id": self.package["publication_id"], "confirmed": True,
            "package_checksum": self.us.package_catalogue()[0]["checksum"]})

    def test_package_is_selected_cloud_config_not_arbitrary_saved_history(self):
        archive = deepcopy(self.package)
        archive["publication_id"] = "archived-us-package"
        self.us.stage_package(archive)
        status, body = self.get("/v1/us/package")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(self.package, body["package"])
        self.assertEqual(HTTPStatus.CONFLICT, self.get("/v1/us/package?publication_id=archived-us-package")[0])
        self.assertIsNotNone(self.us.saved_package("archived-us-package"))

    def test_conductor_cannot_fetch_full_package(self):
        conductor = PairedClient("crew", "Crew", DeviceKind.US_CONDUCTOR, ())
        self.assertEqual(HTTPStatus.FORBIDDEN, self.get("/v1/us/package", conductor)[0])

    def test_eu_selection_and_interrupted_transition_block_live_us_reads(self):
        selected = self.application.lifecycle.selected()
        ticket = self.application.lifecycle.begin_transition("us", selected["meet_id"], "next")
        for path in ("/v1/us/package", "/v1/us/command-status?command_id=x"):
            self.assertEqual(HTTPStatus.CONFLICT, self.get(path)[0])
        self.application.lifecycle.abort_transition(ticket)
        self.application.lifecycle.select("eu", "eu-meet", "eu-package", allow_switch=True)
        for path in ("/v1/us/package", "/v1/us/command-status?command_id=x"):
            self.assertEqual(HTTPStatus.CONFLICT, self.get(path)[0])

    def test_command_status_is_actor_and_current_session_scoped(self):
        started = self.start("start-one")
        status, body = self.get("/v1/us/command-status?command_id=start-one")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(started, body["result"])
        conductor = PairedClient("other", "Other crew", DeviceKind.US_CONDUCTOR, ())
        self.assertIsNone(self.get("/v1/us/command-status?command_id=start-one", conductor)[1]["result"])
        current = self.us.current_session()
        self.application.us_command(self.client, {"action": "finish_session", "command_id": "finish-one",
            "session_id": current["id"], "expected_revision": current["revision"]})
        self.start("start-two")
        self.assertIsNone(self.get("/v1/us/command-status?command_id=start-one")[1]["result"])
        self.assertEqual(started, self.us.command_status(self.actor, "start-one"))
