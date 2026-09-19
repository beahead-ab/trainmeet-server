"""Two real HTTP backends with temporary databases, no MQTT/Cloud network IO.

Only the initial published config source is seeded from the integration test
fixture. Every browser request uses the real HTTP/auth/lifecycle/clock code.
stdin EOF shuts down both servers and removes their temporary SQLite files.
"""
import dataclasses
import json
from pathlib import Path
import sys
import tempfile
import threading

from test_cloud_only_http import CloudOnlyDeliveryTests, us_package
from tmbox_gateway.http_server import TrainMeetHTTPServer


fixtures = []
servers = []
threads = []
with tempfile.TemporaryDirectory(prefix="trainmeet-browser-smoke-") as directory:
    try:
        urls = {}
        for region in ("eu", "us"):
            fixture = CloudOnlyDeliveryTests()
            fixture.setUp()
            fixtures.append(fixture)
            fixture.app.config = dataclasses.replace(
                fixture.app.config, local_development=True,
                state_dir=str(Path(directory) / region),
            )
            if region == "us":
                fixture.offered = us_package()
            fixture.connect()
            fixture.identities.configure_admin_access("smoke-admin", "isolated-browser-test")
            fixture.runtime.save_server_name("Isolated browser " + region.upper())
            fixture.runtime.complete_installation()
            if region == "eu":
                fixture.identities.record_discovery("esp8266-smoke", "TBX-SMOKE", model="ESP8266 test fixture")
            server = TrainMeetHTTPServer(("127.0.0.1", 0), fixture.app)
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            threads.append(thread)
            thread.start()
            urls[region] = f"http://127.0.0.1:{server.server_port}"
        print(json.dumps(urls), flush=True)
        for _ in sys.stdin:
            pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        for fixture in reversed(fixtures):
            fixture.doCleanups()
