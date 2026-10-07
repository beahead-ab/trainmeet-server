"""Träffklockan står still medan servern startar om.

Render startar om servern vid varje driftsättning (med disk, utan överlapp).
Förut räknades avbrottet in: en klocka i 4× hoppade fram fyra minuter för en
minuts omstart. Nu stannar en ordnad nedstängning klockan och starten låter den
gå vidare från samma tid. En klocka som admin själv stoppat förblir stoppad.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from runtime_fixture import runtime_package_v3
from tmbox_gateway.operations import RESTART_STOP_REASON, SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication

BASE = datetime(2026, 10, 7, 2, 50, tzinfo=timezone.utc)
SERVER = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "local_server.py"


class ClockRestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "trainmeet.db"
        self.store = SQLiteOperationsStore(self.path)
        self.store.ensure_publication(RuntimePublication.parse(runtime_package_v3()))  # 4×

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def restart(self):
        """En ny process: en ny store på samma fil."""
        self.store.close()
        self.store = SQLiteOperationsStore(self.path)

    def test_a_running_clock_continues_from_where_it_stood(self):
        self.store.start_clock(time_value="06:20:00", now=BASE)
        stop = BASE + timedelta(seconds=15)  # 06:21:00 i 4×
        self.assertTrue(self.store.pause_clock_for_restart(now=stop))
        paused = self.store.clock_status(now=stop + timedelta(seconds=30))
        self.assertEqual((paused["time"], paused["running"], paused["stopped_reason"]), ("06:21:00", False, RESTART_STOP_REASON))
        self.restart()
        start = stop + timedelta(seconds=60)  # en minuts avbrott
        self.assertTrue(self.store.resume_clock_after_restart(now=start))
        resumed = self.store.clock_status(now=start)
        # Inte 06:25:00: avbrottet räknas inte.
        self.assertEqual((resumed["time"], resumed["running"], resumed["stopped_reason"]), ("06:21:00", True, None))
        self.assertEqual(self.store.clock_status(now=start + timedelta(seconds=15))["time"], "06:22:00")
        self.assertEqual(self.store.clock_status(now=start)["speed"], 4)

    def test_a_clock_the_admin_stopped_stays_stopped(self):
        self.store.start_clock(time_value="06:20:00", now=BASE)
        self.store.stop_clock("Rast")
        before = self.store.clock_status()
        self.assertFalse(self.store.pause_clock_for_restart(now=BASE + timedelta(minutes=5)))
        self.restart()
        self.assertFalse(self.store.resume_clock_after_restart(now=BASE + timedelta(minutes=6)))
        after = self.store.clock_status()
        self.assertEqual((after["time"], after["running"], after["stopped_reason"]), (before["time"], False, "Rast"))

    def test_a_stopped_clock_without_reason_is_not_started(self):
        self.store.start_clock(time_value="06:20:00", now=BASE)
        self.store.stop_clock()
        self.restart()
        self.assertFalse(self.store.resume_clock_after_restart(now=BASE + timedelta(minutes=1)))
        self.assertFalse(self.store.clock_status()["running"])

    def test_resuming_twice_changes_nothing_the_second_time(self):
        self.store.start_clock(time_value="06:20:00", now=BASE)
        self.store.pause_clock_for_restart(now=BASE)
        self.assertTrue(self.store.resume_clock_after_restart(now=BASE + timedelta(seconds=60)))
        self.assertFalse(self.store.resume_clock_after_restart(now=BASE + timedelta(seconds=90)))
        self.assertEqual(self.store.clock_status(now=BASE + timedelta(seconds=75))["time"], "06:21:00")

    def test_without_a_meet_there_is_nothing_to_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "trainmeet.db")
            try:
                self.assertFalse(store.pause_clock_for_restart())
                self.assertFalse(store.resume_clock_after_restart())
            finally:
                store.close()

    def test_with_fastclock_the_internal_clock_is_paused_not_the_external(self):
        """Med FastClock som källa ger clock_status FastClocks tid; pausen gäller den interna raden."""
        self.store.start_clock(time_value="06:20:00", now=BASE)
        external = {"configured": True, "time": "14:00:00", "elapsed_seconds": 50400, "speed": 1, "running": True,
                    "stopped_reason": None, "show_seconds": True, "available_styles": [], "source": "fastclock"}
        self.store.external_clock_source = lambda: external
        self.assertTrue(self.store.pause_clock_for_restart(now=BASE + timedelta(seconds=15)))
        self.assertEqual(self.store.clock_status(), external)
        self.store.external_clock_source = None
        self.assertEqual(self.store.clock_status()["time"], "06:21:00", "the internal time, not FastClock's 14:00")


class ServerProcessRestartTests(unittest.TestCase):
    """Den riktiga servern: SIGTERM stannar klockan, nästa start låter den gå vidare."""

    def setUp(self):
        if shutil.which("mosquitto") is None:
            raise unittest.SkipTest("mosquitto is not installed")
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def free_port():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def run_server(self):
        http, mqtt = self.free_port(), self.free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "tmbox_gateway.local_server", "--state-dir", str(self.state), "--bind", "127.0.0.1",
             "--http-port", str(http), "--mqtt-port", str(mqtt), "--advertised-host", "127.0.0.1", "--gateway-id", "klockprov"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SERVER.parents[1]), os.environ.get("PYTHONPATH")]))},
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{http}/healthz", timeout=1):
                    return process
            except OSError:
                if process.poll() is not None:
                    self.fail(process.stdout.read())
                time.sleep(0.2)
        process.kill()
        self.fail("the server did not start")

    def stop_server(self, process):
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=30)
        return output

    def clock_row(self):
        with sqlite3.connect(self.state / "trainmeet.db") as connection:
            return connection.execute("SELECT base_seconds, running, stopped_reason FROM runtime_clock").fetchone()

    def test_sigterm_pauses_and_the_next_start_resumes(self):
        store = SQLiteOperationsStore(self.state / "trainmeet.db")
        store.ensure_publication(RuntimePublication.parse(runtime_package_v3()))  # 4×
        store.start_clock(time_value="06:20:00")
        store.close()
        output = self.stop_server(self.run_server())
        self.assertIn("Träffklockan står still", output)
        seconds, running, reason = self.clock_row()
        self.assertEqual((running, reason), (0, RESTART_STOP_REASON))
        time.sleep(3)  # avbrottet: 12 s i 4× om det räknades
        server = self.run_server()
        try:
            store = SQLiteOperationsStore(self.state / "trainmeet.db")
            status = store.clock_status()
            store.close()
            self.assertTrue(status["running"])
            self.assertIsNone(status["stopped_reason"])
            # Bara tiden sedan starten räknas (högst några sekunder i 4×), inte avbrottet.
            self.assertLess(status["elapsed_seconds"] - seconds, 8, status)
        finally:
            self.assertIn("Träffklockan går vidare", self.stop_server(server))


if __name__ == "__main__":
    unittest.main()
