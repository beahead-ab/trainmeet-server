"""A real TrainMeet Server with a meet that has only the drivers (Cloud #113).

The package is the one Cloud builds for a meet with köruppdrag but no line
yet: no stations, no stretches, no TMBoxes, and driver duties whose own pages
(a small PDF per duty) the server has fetched with the package. One owner
(Casper) and one driver (Benny). stdin EOF shuts everything down.
"""
import hashlib
import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

PART = b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n" \
       b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n" \
       b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n" \
       b"trailer << /Root 1 0 R >>\n%%EOF\n"

with tempfile.TemporaryDirectory(prefix="trainmeet-lokforaren-cloud-") as directory:
    database = Path(directory) / "trainmeet.db"
    identities = IdentityStore(database)
    runtime = SQLiteRuntimeStore(database)
    operations = SQLiteOperationsStore(database)
    identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
    invited = identities.invite_admin_user("Benny", "benny@example.se", "user")
    identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "forarens-losenord")
    runtime.save_server_name("Prov")
    runtime.complete_installation()
    package = runtime_package_v3(publication_id="drivers-only")
    for key in ("stations", "connections", "panels", "trains", "routes", "services", "tracks", "autonomous_links"):
        package[key] = []
    package["display"] = {"graph_station_order": []}
    package["driver_duties"] = [{
        "id": "duty-81", "number": "81", "operator": "Norske Statsbaner", "start": "17:40", "end": "19:10",
        "trains": [{"number": "RE 2020", "from": "Skuggevik", "to": "Steinsnes"}],
        "events": [
            {"kind": "duty_start", "time": "17:40", "station": "Skuggevik"},
            {"kind": "departure", "time": "17:55", "station": "Skuggevik", "train": "RE 2020", "track": "1"},
            {"kind": "arrival", "time": "18:20", "station": "Steinsnes", "train": "RE 2020", "track": "2"},
            {"kind": "duty_end", "time": "19:10", "station": "Steinsnes"},
        ],
        "source": {"pages": [349, 352], "part": {"sha256": hashlib.sha256(PART).hexdigest(), "size": len(PART)}},
    }]
    publication = runtime.install(package)
    operations.ensure_publication(publication)
    operations.start_clock(time_value="17:51:00", now=datetime.now(timezone.utc))
    app = TrainMeetHTTPApplication(
        TrafficEngine(publication.session_config()), identities, PairingService(identities, set()),
        HTTPServerConfig(local_development=True, state_dir=directory),
        runtime_store=runtime, operations_store=operations,
    )
    app.cloud_driver_parts.ensure(package, lambda sha, size: PART)
    server = TrainMeetHTTPServer(("127.0.0.1", 0), app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({"base": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
    sys.stdin.read()
    server.shutdown()
    server.server_close()
    for store in (operations, runtime, identities):
        store.close()
