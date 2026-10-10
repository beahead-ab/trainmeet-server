"""What changed on the server, for open pages (GET /v1/events).

A page that is told *that* traffic, the clock, the boxes, the meet or the
automatic stations changed fetches it again at once, instead of on its next timer.
The feed carries topic names only, never data: anything a page shows it
still reads from the endpoints it always used, with the access they need.

notify() may be called while the traffic lock is held (after a command
commits) and from the MQTT and clock threads, so it only counts and wakes;
it never waits for a page.
"""
from __future__ import annotations

import secrets
import threading

TOPICS = ("traffic", "clock", "devices", "runtime", "automatic")

# Each open page holds one HTTP worker thread. Enough for Drift, the displays
# and the participants' phones at a meet; past that, a page keeps its timer.
STREAMS_TOTAL = 48
STREAMS_PER_ADDRESS = 6


class ChangeFeed:
    def __init__(self) -> None:
        # Told to every page on connect: a page back after a gap compares it
        # and its last number, and fetches everything again if either moved.
        self.boot = secrets.token_hex(4)
        self._condition = threading.Condition()
        self._seq = 0
        self._latest: dict[str, int] = {}
        self._closed = False
        self._streams: dict[str, int] = {}

    @property
    def seq(self) -> int:
        with self._condition:
            return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def notify(self, *topics: str) -> None:
        unknown = set(topics) - set(TOPICS)
        if not topics or unknown:
            raise ValueError(f"unknown change topic: {sorted(unknown) or 'none'}")
        with self._condition:
            self._seq += 1
            for topic in topics:
                self._latest[topic] = self._seq
            self._condition.notify_all()

    def wait(self, since: int, timeout: float) -> tuple[int, list[str]]:
        """The topics changed after `since`, waiting up to `timeout` for one."""
        with self._condition:
            self._condition.wait_for(lambda: self._closed or self._seq > since, timeout)
            return self._seq, sorted(topic for topic, seq in self._latest.items() if seq > since)

    def close(self) -> None:
        """Server shutdown: every open stream ends now."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def admit(self, address: str) -> bool:
        with self._condition:
            if sum(self._streams.values()) >= STREAMS_TOTAL or self._streams.get(address, 0) >= STREAMS_PER_ADDRESS:
                return False
            self._streams[address] = self._streams.get(address, 0) + 1
            return True

    def release(self, address: str) -> None:
        with self._condition:
            left = self._streams.get(address, 0) - 1
            if left > 0:
                self._streams[address] = left
            else:
                self._streams.pop(address, None)
