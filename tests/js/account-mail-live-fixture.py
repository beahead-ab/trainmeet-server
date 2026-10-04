"""Real TrainMeet Server backends and a fake TrainMeet Cloud for e-mail.

The servers use their real HTTP, identity and Cloud client code. The fake Cloud
answers POST /api/server-mail like the real one (202) and keeps what it was
sent; GET /captured hands that to the browser test. No real e-mail is sent and
no network leaves this machine. stdin EOF shuts everything down.
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore

captured = []


class FakeCloud(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        captured.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": body})
        self._json(202, {"queued": True})

    def do_GET(self):  # noqa: N802
        self._json(200, captured)

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


servers, threads, closers = [], [], []


def serve(server):
    servers.append(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    threads.append(thread)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}"


with tempfile.TemporaryDirectory(prefix="trainmeet-account-mail-") as directory:
    try:
        cloud = serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeCloud))
        urls = {"cloud": cloud}
        for name in ("linked", "offline"):
            identities = IdentityStore(Path(directory) / f"{name}-identity.db")
            runtime = SQLiteRuntimeStore(Path(directory) / f"{name}-runtime.db")
            closers += [identities.close, runtime.close]
            identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
            invited = identities.invite_admin_user("Benny", "benny@example.se", "admin")
            identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "bennys-losenord")
            runtime.save_server_name(f"Prov {name}")
            runtime.complete_installation()
            if name == "linked":
                runtime.save_link_token("kopplingsnyckel-i-provet")
            engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
            app = TrainMeetHTTPApplication(
                engine, identities, PairingService(identities, set(engine.config.panels)),
                HTTPServerConfig(local_development=True, central_runtime_url=f"{cloud}/config",
                                 state_dir=str(Path(directory) / name)),
                runtime_store=runtime,
            )
            urls[name] = serve(TrainMeetHTTPServer(("127.0.0.1", 0), app))
        print(json.dumps(urls), flush=True)
        for _ in sys.stdin:
            pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        for close in reversed(closers):
            close()
