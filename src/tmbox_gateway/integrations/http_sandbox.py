"""Cookie-isolated HTTP adapter for the synthetic loopback service only."""
from __future__ import annotations

from dataclasses import asdict
from http.client import HTTPException
from http.cookiejar import CookieJar
import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener,
)

from .framework import (
    Command, Connection, IntegrationError, Outcome, Scope, ScopeMismatch,
    SessionExpired, Train,
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpSandboxAdapter:
    simulated = True
    capabilities = frozenset({"timetable", "status", "clear", "command_status"})

    def __init__(self, base_url: str, account_id: str, password: str, *, timeout: float = 1):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
                or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/")):
            raise ValueError("Sandbox HTTP requires a numeric IPv4 loopback origin")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A finite positive timeout is required")
        self._base_url = base_url.rstrip("/")
        self._account_id = account_id
        self._password = password
        self._timeout = timeout
        self._cookies = CookieJar()
        # Never inherit environment proxies, global cookies or redirect behavior.
        self._opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self._cookies), _NoRedirect())
        self._active = False

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        request = Request(self._base_url + path, method=method,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise IntegrationError("Sandbox response exceeds limit")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                return result
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 401:
                self._active = False
                raise SessionExpired("Sandbox authentication required") from None
            raise IntegrationError("Sandbox HTTP request failed") from None
        except (URLError, OSError, HTTPException, ValueError, UnicodeError):
            # Provider bodies/URLs/credentials never appear in public exceptions.
            raise IntegrationError("Sandbox transport or response failed") from None

    @staticmethod
    def _scope(data: dict) -> Scope:
        try:
            return Scope(**data["scope"])
        except (KeyError, TypeError, ValueError):
            raise IntegrationError("Invalid sandbox identity") from None

    def authenticate(self, connection: Connection) -> Scope:
        self.close()
        data = self._request("POST", "/session", {
            "account_id": self._account_id, "password": self._password,
        })
        # Set active before decoding so a failed identity still releases its session.
        self._active = True
        return self._scope(data)

    def identity(self) -> Scope:
        if not self._active:
            raise SessionExpired("Sandbox authentication required")
        return self._scope(self._request("GET", "/identity"))

    def timetable(self) -> tuple[Train, ...]:
        data = self._request("GET", "/timetable")
        try:
            rows = data["trains"]
            if not isinstance(rows, list):
                raise ValueError("Expected list")
            trains = tuple(Train(**row) for row in rows)
            if any(not isinstance(t.train_id, str) or not t.train_id
                   or t.status not in {"scheduled", "cleared"} for t in trains):
                raise ValueError("Invalid train")
            return trains
        except (KeyError, TypeError, ValueError):
            raise IntegrationError("Invalid sandbox timetable") from None

    def train_status(self, train_id: str) -> str:
        data = self._request("GET", "/trains/" + quote(train_id, safe=""))
        if data.get("train_id") != train_id or data.get("status") not in ("scheduled", "cleared"):
            raise IntegrationError("Invalid sandbox train status")
        return data["status"]

    @staticmethod
    def _receipt(data: dict, command: Command) -> None:
        if (data.get("command") != asdict(command) or data.get("accepted") is not True
                or not isinstance(data.get("completed"), bool)):
            raise ScopeMismatch("Sandbox receipt does not match command")

    def execute(self, command: Command) -> bool:
        data = self._request("POST", "/commands", asdict(command))
        self._receipt(data, command)
        return True

    def command_status(self, command: Command) -> Outcome:
        data = self._request("GET", "/commands/" + quote(command.command_id, safe=""))
        self._receipt(data, command)
        if data["completed"] and self.train_status(command.train_id) == "cleared":
            return Outcome.CONFIRMED
        return Outcome.PENDING

    def close(self) -> None:
        try:
            if self._active:
                self._request("DELETE", "/session")
        except IntegrationError:
            pass
        finally:
            self._active = False
            self._cookies.clear()
