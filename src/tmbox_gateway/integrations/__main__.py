"""Run a local, explicitly simulated compatibility demonstration."""
import argparse
from dataclasses import asdict
import json

from .framework import Command, Connection, IntegrationService, Scope
from .stub import StubAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-test", action="store_true", help="Also modify the in-memory test train")
    args = parser.parse_args()
    scope = Scope("demo-meet", "stub", "demo-account", "demo-station")
    service = IntegrationService({"stub": StubAdapter})
    service.register(Connection(scope, enabled=True, sandbox=True))
    try:
        service.connect(scope)
        report = service.preflight(scope, test_command=(
            Command("demo-clear-101", "test-101") if args.write_test else None
        ))
        print(json.dumps({**asdict(report), "passed": report.passed}, indent=2))
    finally:
        service.disconnect(scope)


if __name__ == "__main__":
    main()
