"""Startar local_server med tidtagning och konstgjord långsamhet. Rör inte koden.

    python3 timed_server.py <timing.jsonl> <local_server-argument ...>

STALL_AT/STALL_SECONDS: en enda spärr på arbetaren, med trafiklåset taget.
SLOW_MS: så mycket extra tid per meddelande arbetaren faktiskt arbetar med,
med trafiklåset taget - som en långsam databasskrivning skulle vara.

Långsamheten läggs där arbetet görs under trafiklåset: i on_message till och
med 1.17.0, i _handle_current därefter. Då går samma scenario att köra mot båda.
"""
import json
import os
import sys
import threading
import time

from tmbox_gateway import local_server, terminal16_mqtt

LOG = open(sys.argv.pop(1), "a", buffering=1)
Gateway = terminal16_mqtt.Terminal16Gateway
WORK = "_handle_current" if hasattr(Gateway, "_handle_current") else "on_message"
STALL_AT = float(os.environ.get("STALL_AT", "0"))
STALL_SECONDS = float(os.environ.get("STALL_SECONDS", "0"))
SLOW_MS = float(os.environ.get("SLOW_MS", "0"))
STARTED = time.monotonic()
_stalled = []


def record(what, started, leaf=""):
    LOG.write(json.dumps({"t": time.time(), "what": what, "leaf": leaf,
                          "ms": round((time.perf_counter() - started) * 1000, 2),
                          "thread": threading.current_thread().name}) + "\n")


def timed(name, function):
    def wrapper(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return function(self, *args, **kwargs)
        finally:
            leaf = ""
            if name == "on_message" and args:
                leaf = args[0].rsplit("/", 1)[-1]
            elif name == "_handle_current" and len(args) > 1:
                leaf = args[1]
            record(name, started, leaf)
    return wrapper


def burdened(function):
    def wrapper(self, *args, **kwargs):
        lock = self.terminals.service.operations_store.command_lock
        if STALL_AT and not _stalled and time.monotonic() - STARTED >= STALL_AT:
            _stalled.append(1)
            with lock:
                LOG.write(json.dumps({"t": time.time(), "what": "STALL", "ms": STALL_SECONDS * 1000}) + "\n")
                time.sleep(STALL_SECONDS)
        if SLOW_MS:
            with lock:
                time.sleep(SLOW_MS / 1000)
        return function(self, *args, **kwargs)
    return wrapper


setattr(Gateway, WORK, timed(WORK, burdened(getattr(Gateway, WORK))))
Gateway.tick = timed("tick", Gateway.tick)
Gateway._frame = timed("_frame", Gateway._frame)
local_server.main()
