"""A real TrainMeet Server running a meet that so far only has its drivers.

The first layer of a meet (Cloud #113): the duty sheets, but no layout yet,
so no stations, trains or TMBox panels. Duty 81 has its own pages as a PDF
part in Cloud; the server fetches it with its server link. Here the fetch is
replaced by one that answers with a small drawn PDF, and counts the calls.
stdin EOF shuts everything down.
"""
import hashlib
import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

PART = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
SHA = hashlib.sha256(PART).hexdigest()

with tempfile.TemporaryDirectory(prefix="trainmeet-lokforare-cloud-") as directory:
    database = Path(directory) / "trainmeet.db"
    identities = IdentityStore(database)
    runtime = SQLiteRuntimeStore(database)
    operations = SQLiteOperationsStore(database)
    identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
    invited = identities.invite_admin_user("Benny", "benny@example.se", "user")
    identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "forarens-losenord")
    runtime.save_server_name("Prov")
    runtime.complete_installation()
    runtime.save_central_url("https://cloud.example/config")
    runtime.save_link_token("server-link-token")
    package = runtime_package_v3(publication_id="publication-drivers-only")
    for key in ("stations", "connections", "trains", "panels", "routes", "services", "tracks", "autonomous_links"):
        package[key] = []
    package["display"]["graph_station_order"] = []

    def duty(number, first, last, **extra):
        return {"id": f"duty-{number}", "number": number, "operator": "Provbolaget", "start": "13:20", "end": "14:30",
                "trains": [{"number": f"RE {number}0", "from": first, "to": last}],
                "events": [{"kind": "duty_start", "time": "13:20", "station": first},
                           {"kind": "departure", "time": "13:30", "station": first, "train": f"RE {number}0", "track": "1"},
                           {"kind": "arrival", "time": "14:10", "station": last, "train": f"RE {number}0", "track": "2"},
                           {"kind": "duty_end", "time": "14:30", "station": last}], **extra}

    package["driver_duties"] = [
        duty("81", "Norrby", "Söderås", source={"pages": [3, 4], "part": {"sha256": SHA, "size": len(PART)}}),
        duty("82", "Söderås", "Norrby"),
    ]
    publication = runtime.install(package)
    operations.ensure_publication(publication)
    operations.start_clock(time_value="13:25:00", now=datetime.now(timezone.utc))
    engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
    app = TrainMeetHTTPApplication(
        engine, identities, PairingService(identities, set(engine.config.panels)),
        HTTPServerConfig(local_development=True, state_dir=directory),
        runtime_store=runtime, operations_store=operations,
    )
    calls = []

    def fetch(token, url, sha):
        calls.append(sha)
        return PART

    app.driver_part_fetcher = fetch
    server = TrainMeetHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"base": f"http://127.0.0.1:{server.server_address[1]}", "meet": package["meet"]["name"],
                      "slug": package["meet"]["slug"], "sha": SHA}), flush=True)
    sys.stdin.read()
    # Hämtningen ska ha gått en gång, i bakgrunden eller när föraren öppnade sidorna.
    print(json.dumps({"fetches": len(calls)}), flush=True)
    server.shutdown()
    server.server_close()
    for store in (operations, runtime, identities):
        store.close()
