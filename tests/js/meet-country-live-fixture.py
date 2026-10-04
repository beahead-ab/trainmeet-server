"""En riktig server med en dansk träff, levererad som från TrainMeet Cloud.

Samma Cloud-fixtur som server-shell-live, men paketet bär meet.country "dk".
Temporär databas, inget nätverk utåt. stdin EOF stänger servern."""
import dataclasses, json, sys, tempfile, threading
from pathlib import Path
from test_cloud_only_http import CloudOnlyDeliveryTests
from tmbox_gateway.http_server import TrainMeetHTTPServer
with tempfile.TemporaryDirectory() as directory:
    fixture = CloudOnlyDeliveryTests(); fixture.setUp()
    fixture.app.config = dataclasses.replace(fixture.app.config, local_development=True, state_dir=directory)
    fixture.offered["meet"]["country"] = "dk"; fixture.offered["meet"]["default_language"] = "da"
    fixture.offered["meet"]["name"] = "Givskud modelbanetræf"
    fixture.connect()
    fixture.identities.create_first_owner("Smoke", "smoke-admin@example.se", "isolated-browser-test")
    fixture.runtime.save_server_name("Givskud")
    fixture.runtime.complete_installation()
    server = TrainMeetHTTPServer(("127.0.0.1", 0), fixture.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    print(json.dumps({"eu": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    for _ in sys.stdin: pass
    server.shutdown(); server.server_close(); fixture.doCleanups()
