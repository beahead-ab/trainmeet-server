"""Run a local, explicitly simulated compatibility demonstration."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

from .framework import Command, Connection, IntegrationService, Scope
from .http_sandbox import HttpSandboxAdapter
from .journal import SqliteCommandJournal
from .sandbox_http import SandboxServer
from .stub import StubAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-test", action="store_true", help="Also modify the in-memory test train")
    parser.add_argument("--http", action="store_true", help="Use a local HTTP simulator with isolated cookies")
    parser.add_argument("--journal", type=Path, help="Dedicated SQLite command journal path")
    args = parser.parse_args()
    scope = Scope("demo-meet", "stub", "demo-account", "demo-station")
    if args.http:
        scope = Scope("demo-meet", "http-sandbox", "demo-account", "demo-station")
        with SandboxServer((scope,)) as sandbox:
            run_demo(scope, lambda: HttpSandboxAdapter(
                sandbox.base_url, scope.account_id, sandbox.test_password(scope.account_id)
            ), args)
    else:
        run_demo(scope, StubAdapter, args)


def run_demo(scope, factory, args):
    service = IntegrationService({scope.provider_id: factory}, journal=SqliteCommandJournal(
        args.journal if args.journal else ":memory:"
    ))
    try:
        service.register(Connection(scope, enabled=True, sandbox=True))
        service.connect(scope)
        report = service.preflight(scope, test_command=(
            Command("demo-clear-" + uuid4().hex, "test-101") if args.write_test else None
        ))
        print(json.dumps({**asdict(report), "passed": report.passed}, indent=2))
    finally:
        service.close()


if __name__ == "__main__":
    main()
