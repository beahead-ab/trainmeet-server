"""Startar local_server med tidtagning runt gatewayens arbete. Rör inte koden."""
import json, sys, threading, time
from tmbox_gateway import terminal16_mqtt, local_server

LOG = open(sys.argv.pop(1), "a", buffering=1)
original_on_message = terminal16_mqtt.Terminal16Gateway.on_message
original_tick = terminal16_mqtt.Terminal16Gateway.tick
original_frame = terminal16_mqtt.Terminal16Gateway._frame

def timed(name, function):
    def wrapper(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return function(self, *args, **kwargs)
        finally:
            leaf = args[0].rsplit("/", 1)[-1] if name == "on_message" and args else ""
            LOG.write(json.dumps({"t": time.time(), "what": name, "leaf": leaf,
                                  "ms": round((time.perf_counter() - started) * 1000, 2),
                                  "thread": threading.current_thread().name}) + "\n")
    return wrapper

terminal16_mqtt.Terminal16Gateway.on_message = timed("on_message", original_on_message)
terminal16_mqtt.Terminal16Gateway.tick = timed("tick", original_tick)
terminal16_mqtt.Terminal16Gateway._frame = timed("_frame", original_frame)
import os
STALL_AT = float(os.environ.get("STALL_AT", "0"))
STALL_SECONDS = float(os.environ.get("STALL_SECONDS", "0"))
STARTED = time.monotonic()
_stalled = []
_inner = terminal16_mqtt.Terminal16Gateway.on_message
def stalling(self, topic, payload, **kwargs):
    if STALL_AT and not _stalled and time.monotonic() - STARTED >= STALL_AT:
        _stalled.append(1)
        with self.terminals.service.operations_store.command_lock:
            LOG.write(json.dumps({"t": time.time(), "what": "STALL", "ms": STALL_SECONDS * 1000}) + "\n")
            time.sleep(STALL_SECONDS)
    return _inner(self, topic, payload, **kwargs)
SLOW_MS = float(os.environ.get("SLOW_MS", "0"))
def slow(self, topic, payload, **kwargs):
    # Ett långsamt meddelande med trafiklåset taget, som en långsam
    # databasskrivning på ett SD-kort skulle vara.
    if SLOW_MS:
        with self.terminals.service.operations_store.command_lock:
            time.sleep(SLOW_MS / 1000)
    return stalling(self, topic, payload, **kwargs)
terminal16_mqtt.Terminal16Gateway.on_message = slow
local_server.main()
