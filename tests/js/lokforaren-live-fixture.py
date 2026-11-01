"""A real TrainMeet Server for the Lokföraren browser test.

One owner (Casper) and two drivers (Benny, Maja, role user). The meet comes
from the runtime fixture with one driver duty in the Cloud package format, and
the meet clock runs at 4× from 17:51. stdin EOF shuts everything down.
"""
import json
import os
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

with tempfile.TemporaryDirectory(prefix="trainmeet-lokforaren-") as directory:
    database = Path(directory) / "trainmeet.db"
    identities = IdentityStore(database)
    runtime = SQLiteRuntimeStore(database)
    operations = SQLiteOperationsStore(database)
    identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
    for name, email in (("Benny", "benny@example.se"), ("Maja", "maja@example.se")):
        invited = identities.invite_admin_user(name, email, "user")
        identities.redeem_admin_setup(email, str(invited["setup_code"]), "forarens-losenord")
    runtime.save_server_name("Prov")
    runtime.complete_installation()
    package = runtime_package_v3()
    stations = [station["name"] for station in package["stations"]]
    package["driver_duties"] = [{
        "id": "duty-7", "number": "7", "operator": "Provbolaget", "start": "17:40", "end": "19:10",
        "trains": [{"number": "P 101", "from": stations[0], "to": stations[-1]}],
        "events": [
            {"kind": "duty_start", "time": "17:40", "station": stations[0]},
            {"kind": "departure", "time": "17:55", "station": stations[0], "train": "P 101", "track": "1"},
            {"kind": "arrival", "time": "18:20", "station": stations[-1], "train": "P 101", "track": "2", "notes": ["Koppla loss"]},
            {"kind": "duty_end", "time": "19:10", "station": stations[-1]},
        ],
    }]
    if os.environ.get("LOKFORAREN_FULL_CATALOG"):
        template = package["driver_duties"][0]
        package["driver_duties"] = [dict(template, id=f"catalog-{n}", number=str(n),
            operator="SJ" if n % 2 else "CargoNet", vehicle=f"Rc {n}",
            start="18:00" if n % 2 else "17:00", end="20:00") for n in range(1, 143)]
    publication = runtime.install(package)
    operations.ensure_publication(publication)
    operations.start_clock(time_value="17:51:00", now=datetime.now(timezone.utc))
    engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
    app = TrainMeetHTTPApplication(
        engine, identities, PairingService(identities, set(engine.config.panels)),
        HTTPServerConfig(local_development=True, state_dir=directory),
        runtime_store=runtime, operations_store=operations,
    )
    server = TrainMeetHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"base": f"http://127.0.0.1:{server.server_address[1]}", "stations": stations}), flush=True)
    sys.stdin.read()
    server.shutdown()
    server.server_close()
    for store in (operations, runtime, identities):
        store.close()
