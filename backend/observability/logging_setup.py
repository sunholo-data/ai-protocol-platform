"""Application logging configuration (issue #39).

Until this existed the backend configured **no** logging at all: the root
logger sat at its default WARNING, so every ``logger.info`` from our own
modules was dropped before it left the process. The visible symptom was that
`LatencyTracker.emit_log` — which computes a full per-stage TTFT breakdown on
*every* chat turn — produced nothing in Cloud Logging, so we could not answer
"how fast was that session for the customer?" despite measuring it all along.
(uvicorn's ``INFO:`` lines were still visible, which disguised the problem:
uvicorn configures its own logger.)

Two things are set up here:

**Level** — from ``LOG_LEVEL`` (default ``INFO``). Set ``LOG_LEVEL=WARNING`` to
get the old behaviour back.

**Shape** — on Cloud Run, a JSON line on stdout is parsed by the logging agent
into ``jsonPayload``, with ``severity`` and ``message`` lifted out. That is what
makes ``jsonPayload.event="ttft"`` queryable, and it needs no Cloud Logging
handler (which would add a background thread and a network dependency to every
request path). Off Cloud Run we keep human-readable text.

The `extra={"json_fields": {...}}` convention is the same one
``google-cloud-logging``'s handler uses, so call sites do not change if we ever
switch to it.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# Cloud Run sets K_SERVICE on every instance. Presence of it is the signal that
# stdout is being scraped by the logging agent and JSON will be parsed.
_ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))

# Python level name → Cloud Logging severity. Cloud Logging rejects unknown
# severities, so anything unmapped falls back to the level name itself, which
# is already valid for the standard levels.
_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

# LogRecord attributes that are never payload — everything else a caller puts on
# the record via `extra=` is carried through.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
        "json_fields",
    }
)

_configured = False


class StructuredFormatter(logging.Formatter):
    """Render a record as a single Cloud-Logging-parseable JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "severity": _SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        # `extra={"json_fields": {...}}` — the structured payload proper.
        fields = getattr(record, "json_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        # Anything else passed via `extra=` (but not the stdlib's own attrs).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_") and key not in payload:
                if isinstance(value, (str, int, float, bool, type(None))):
                    payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, default=str)
        except Exception:  # never let logging raise on the request path
            return json.dumps({"severity": payload["severity"], "message": payload["message"]})


def setup_logging() -> str:
    """Configure root logging. Idempotent; returns the level that was applied.

    Safe to call before anything else in the process — it replaces handlers on
    the root logger rather than adding to them, so a re-import cannot double-log.
    """
    global _configured
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
        level_name = "INFO"

    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return level_name

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        StructuredFormatter() if _ON_CLOUD_RUN else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root.handlers[:] = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; let its records flow through ours
    # instead of being emitted twice in two different shapes.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True

    _configured = True
    return level_name


__all__ = ["StructuredFormatter", "setup_logging"]
