"""Real HTTP/login and controller, deterministic fake host. No actual Wi-Fi IO."""
import json
import sys
import threading
from unittest.mock import patch
from test_cloud_only_http import CloudOnlyDeliveryTests
from pi_network_fixture import FakeNetwork
from tmbox_gateway.pi_network import NetworkController
from tmbox_gateway.http_server import TrainMeetHTTPServer

fixture = CloudOnlyDeliveryTests()
fixture.setUp()
fixture.connect()
fixture.identities.create_first_owner("Smoke", "smoke-admin@example.se", "isolated-browser-test")
fixture.runtime.save_server_name("Network test")
fixture.runtime.complete_installation()
controller = NetworkController(FakeNetwork())
server = TrainMeetHTTPServer(("127.0.0.1", 0), fixture.app)
with patch("tmbox_gateway.http_server.pi_network.host_request", side_effect=controller.handle):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    try:
        for _ in sys.stdin: pass
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        fixture.doCleanups()
