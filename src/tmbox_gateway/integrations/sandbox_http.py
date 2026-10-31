"""Local synthetic traffic service for integration tests. Never binds externally."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
from threading import RLock, Thread
from time import sleep
from urllib.parse import unquote

from .framework import Command, Scope


class SandboxServer(AbstractContextManager):
    """Ephemeral accounts/sessions and traffic; command effects outlive client restarts.

Each fixture account is assigned its scope by the SERVER, not by login input.
The server intentionally does not persist across its own shutdown. Fault controls
are Python test helpers only; there are no public fault-injection endpoints.
"""

    def __init__(self, scopes: tuple[Scope, ...]):
        self._accounts = {scope.account_id: scope for scope in scopes}
        if len(self._accounts) != len(scopes):
            raise ValueError("Sandbox accounts must have distinct account IDs")
        self._passwords = {account: secrets.token_urlsafe(24) for account in self._accounts}
        self._sessions: dict[str, Scope] = {}
        self._trains = {scope: {"test-101": "scheduled"} for scope in scopes}
        self._receipts: dict[tuple[Scope, str], dict] = {}
        self._executions = {scope: 0 for scope in scopes}
        self._lose_response: set[Scope] = set()
        self._defer: set[Scope] = set()
        self._read_faults: dict[Scope, str] = {}
        self._lock = RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Test passwords and session identifiers must not enter access logs.
                pass

            def _reply(self, status: int, data: dict, cookie: str | None = None):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected when a timeout test has already closed its socket.

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 8192:
                    raise ValueError("Invalid body length")
                result = json.loads(self.rfile.read(length))
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                return result

            def _identity(self) -> tuple[str, Scope | None]:
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                token = cookie["sandbox_session"].value if "sandbox_session" in cookie else ""
                return token, owner._sessions.get(token)

            def do_POST(self):
                try:
                    data = self._body()
                except (ValueError, UnicodeError):
                    self._reply(400, {"error": "invalid_request"})
                    return
                with owner._lock:
                    if self.path == "/session":
                        account = data.get("account_id")
                        if not isinstance(account, str) or account not in owner._accounts:
                            self._reply(401, {"error": "authentication_required"})
                            return
                        password = data.get("password")
                        if not isinstance(password, str) or not secrets.compare_digest(
                            password, owner._passwords[account]
                        ):
                            self._reply(401, {"error": "authentication_required"})
                            return
                        token = secrets.token_urlsafe(32)
                        scope = owner._accounts[account]
                        owner._sessions[token] = scope
                        self._reply(200, {"scope": asdict(scope)},
                                    f"sandbox_session={token}; Path=/; HttpOnly; SameSite=Strict")
                        return
                    _, scope = self._identity()
                    if scope is None:
                        self._reply(401, {"error": "authentication_required"})
                        return
                    if self.path != "/commands":
                        self._reply(404, {"error": "not_found"})
                        return
                    try:
                        command = Command(**data)
                        if not all(isinstance(v, str) and v.strip() for v in (
                            command.command_id, command.train_id, command.action
                        )):
                            raise ValueError("Invalid command")
                    except (TypeError, ValueError):
                        self._reply(400, {"error": "invalid_command"})
                        return
                    if command.action != "clear" or command.train_id not in owner._trains[scope]:
                        self._reply(400, {"error": "unsupported_command"})
                        return
                    key = (scope, command.command_id)
                    receipt = owner._receipts.get(key)
                    if receipt and receipt["command"] != asdict(command):
                        self._reply(409, {"error": "command_conflict"})
                        return
                    if receipt is None:
                        completed = scope not in owner._defer
                        owner._defer.discard(scope)
                        receipt = {"command": asdict(command), "accepted": True, "completed": completed}
                        owner._receipts[key] = receipt
                        owner._executions[scope] += 1
                        if completed:
                            owner._trains[scope][command.train_id] = "cleared"
                    if scope in owner._lose_response:
                        owner._lose_response.discard(scope)
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self._reply(200, receipt)

            def do_GET(self):
                with owner._lock:
                    _, scope = self._identity()
                    fault = owner._read_faults.pop(scope, None)
                    status = 200
                    if scope is None:
                        status, data = 401, {"error": "authentication_required"}
                    elif self.path == "/identity":
                        data = {"scope": asdict(scope)}
                    elif self.path == "/timetable":
                        data = {"trains": [
                            {"train_id": tid, "status": status}
                            for tid, status in owner._trains[scope].items()
                        ]}
                    elif self.path.startswith("/trains/"):
                        train_id = unquote(self.path[len("/trains/"):])
                        if train_id in owner._trains[scope]:
                            data = {"train_id": train_id, "status": owner._trains[scope][train_id]}
                        else:
                            status, data = 404, {"error": "not_found"}
                    elif self.path.startswith("/commands/"):
                        command_id = unquote(self.path[len("/commands/"):])
                        receipt = owner._receipts.get((scope, command_id))
                        if receipt is None:
                            status, data = 404, {"error": "not_found"}
                        else:
                            data = dict(receipt)
                    else:
                        status, data = 404, {"error": "not_found"}
                # Fault waits happen outside the shared state lock, so another
                # station's requests remain independent.
                if fault == "timeout":
                    sleep(0.3)
                elif fault == "rate_limit":
                    self._reply(429, {"error": "rate_limited"})
                    return
                elif fault == "invalid_json":
                    self.send_response(200)
                    self.send_header("Content-Length", "1")
                    self.end_headers()
                    self.wfile.write(b"{")
                    return
                elif fault == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._reply(status, data)

            def do_DELETE(self):
                with owner._lock:
                    token, _ = self._identity()
                    if self.path != "/session":
                        self._reply(404, {"error": "not_found"})
                        return
                    owner._sessions.pop(token, None)
                    self._reply(200, {}, "sandbox_session=; Max-Age=0; Path=/")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = Thread(target=self._server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)

    def test_password(self, account_id: str) -> str:
        """Ephemeral fixture secret for local clients; never serialize in reports."""
        return self._passwords[account_id]

    def expire_sessions(self, scope: Scope) -> None:
        with self._lock:
            self._sessions = {token: bound for token, bound in self._sessions.items() if bound != scope}

    def lose_next_write_response(self, scope: Scope) -> None:
        with self._lock:
            self._lose_response.add(scope)

    def defer_next_command(self, scope: Scope) -> None:
        with self._lock:
            self._defer.add(scope)

    def fail_next_read(self, scope: Scope, kind: str) -> None:
        if kind not in {"timeout", "rate_limit", "invalid_json", "redirect"}:
            raise ValueError("Unknown synthetic read fault")
        with self._lock:
            self._read_faults[scope] = kind

    def complete_command(self, scope: Scope, command_id: str) -> None:
        with self._lock:
            receipt = self._receipts[(scope, command_id)]
            receipt["completed"] = True
            self._trains[scope][receipt["command"]["train_id"]] = "cleared"

    def executions(self, scope: Scope) -> int:
        with self._lock:
            return self._executions[scope]
