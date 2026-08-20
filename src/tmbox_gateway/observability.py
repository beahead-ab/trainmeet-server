"""Structured logging and the correlation id that ties a command together.

One command touches the MQTT surface, the traffic engine, the operations
layer and the audit journal. Without a shared id those are four unrelated
lines in a log file, and answering "what happened to that train" means
guessing from timestamps.

Secrets never reach the log. Pairing codes, access tokens and passwords are
redacted by field name on the way out, so a careless caller cannot leak one
by handing it to a log call.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


#: Field names whose values are replaced before anything is written.
SECRET_FIELDS = frozenset(
    {
        "password",
        "pairing_code",
        "connection_code",
        "access_token",
        "token",
        "credential",
        "device_token",
        "secret",
        "session",
    }
)

REDACTED = "[dold]"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def correlation_id() -> str:
    return _correlation_id.get()


@contextmanager
def use_correlation(value: str) -> Iterator[str]:
    """Tag everything logged inside this block with one id."""
    token = _correlation_id.set(value or "")
    try:
        yield value
    finally:
        _correlation_id.reset(token)


def redact(field: str, value: Any) -> Any:
    lowered = field.lower()
    if any(secret in lowered for secret in SECRET_FIELDS):
        return REDACTED
    return value


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured line.

    The event name is the stable part a reader greps for; the fields are what
    varies. Values are redacted by field name, never by inspecting content.
    """
    logger.log(
        level,
        event,
        extra={"event": event, "fields": {key: redact(key, value) for key, value in fields.items()}},
    )


class StructuredFormatter(logging.Formatter):
    """Render records as timestamp plus key=value pairs.

    Plain text so a terminal on a Raspberry Pi stays readable, but parsable
    so a whole command can be pulled out with one grep on its correlation id.
    """

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            f"level={record.levelname}",
            f"logger={record.name}",
        ]
        current = correlation_id()
        if current:
            parts.append(f"correlation_id={_quote(current)}")
        event = getattr(record, "event", None)
        if event:
            parts.append(f"event={_quote(event)}")
        else:
            parts.append(f"message={_quote(record.getMessage())}")
        for key, value in (getattr(record, "fields", None) or {}).items():
            parts.append(f"{key}={_quote(value)}")
        if record.exc_info:
            parts.append(f"exception={_quote(self.formatException(record.exc_info))}")
        return " ".join(parts)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _quote(value: Any) -> str:
    text = "-" if value is None else str(value)
    if text == "":
        text = '""'
    if any(character.isspace() for character in text) or '"' in text:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return text
