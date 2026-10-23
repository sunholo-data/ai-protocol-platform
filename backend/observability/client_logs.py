"""``POST /api/client-logs`` — the browser's only route into Cloud Logging (FRONTEND-OBS M2).

The client tier had no telemetry at all until this existed: 45 ``console.*``
calls whose event names were already structured, dying in a console nobody
watched. ``frontend/src/lib/logger.ts`` batches them here; this route re-emits
each one through the root logger, and ``logging_setup.StructuredFormatter``
renders it as the same Cloud-Logging-parseable JSON line the backend already
writes. There is no new logging machinery: a browser event lands in the same
sink, with the same query surface, as ``event:"ttft"``.

WHY THIS ROUTE IS MOSTLY REFUSALS
It is public-internet-reachable, which makes it the one piece of attack surface
the feature adds, and it is the shape of endpoint where a wrong-but-plausible
implementation passes shallow tests: one that accepts anonymous writes, or
trusts a ``uid`` in the body, looks perfectly healthy from a frontend that
always sends a real token. So:

  * Identity comes from the verified token and NEVER the body. There is no
    comparison to get wrong — ``uid``/``email`` are simply not in the attribute
    allowlist, so a forged one is dropped and counted like any other junk key.
  * Caps are REJECTED, not truncated. A silently truncated batch looks like a
    working endpoint right up to the incident where the one dropped event was
    the one that mattered. A rejected batch logs nothing — partial acceptance
    is truncation wearing a 202.
  * Unknown attribute keys are dropped and counted. Inside the GCP edge, Axiom
    #8 would permit full content; the allowlist is a volume-and-blast-radius
    decision, not a privacy one, and should be argued on those terms.
  * The per-uid limit drops and counts. It never queues: a retry queue that
    survives an outage is how a logger turns a backend blip into unbounded
    memory, on whichever side holds it.

WHY THE CAPS ARE WHAT THEY ARE
The design doc's first draft said 64KB body / 4KB event / 8KB stack, and those
numbers do not compose: the shipped client truncates a stack to 8KB and batches
20 events, so one burst of errors is ~20 x (4KB + 8KB) and a 64KB cap would
refuse the whole batch and lose every event in it. The per-event cap therefore
EXCLUDES the stack fields, which carry their own cap, and the body cap is sized
so the shipped client can never be refused. The real volume control is the
per-uid rate limit, not the byte caps — those exist to bound a single hostile
POST, and 512KB does that.

RATE-LIMIT STATE IS PER INSTANCE. Cloud Run scales out, so the effective limit
is ``instances x limit``. This is a cost guard, not a security control — do not
mistake it for one.

The Error Reporting envelope is verified against
cloud.google.com/error-reporting/docs/formatting-error-messages: when ``@type``
is absent, Cloud Logging looks for ``serviceContext`` to decide an entry is a
ReportedErrorEvent, and a ``message`` that carries a stack trace is what makes
it GROUP by frame rather than by event name. ``@type`` is deliberately NOT
emitted: BigQuery cannot take a column named ``@type``, and the telemetry sink
was silently dropping every ERROR line while INFO and WARNING landed (found on
prod, 2026-09-05). Only ``ERROR`` severity gets the envelope — over-reporting
makes the alerts as useless as under-reporting.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auth import User, get_current_user
from auth.group_rate_limit import RateLimitExceeded, TokenBucketRateLimiter

# The logger name is the second discriminator after ``source``: Cloud Logging
# can filter ``jsonPayload.logger="browser"`` as well as ``source="browser"``.
LOGGER_NAME = "browser"

MAX_BODY_BYTES = 512 * 1024
MAX_BATCH_EVENTS = 50
# Measured on the event WITHOUT its stack fields — see the module docstring.
MAX_EVENT_BYTES = 4_096
# Matches ``MAX_STACK_CHARS`` in frontend/src/lib/logger.ts. A legitimate client
# truncates before sending, so anything over this is forged or broken.
MAX_STACK_CHARS = 8_192
MAX_ATTR_CHARS = 1_024
DEFAULT_RATE_LIMIT_PER_MIN = 100

_STACK_FIELDS = frozenset({"stack", "componentStack"})

# Everything the client may say about an event. NOT here, on purpose: any
# identity key (uid, email, user…) — identity is the token's — and any key the
# server sets itself (source, severity, message, logger, serviceContext).
ALLOWED_ATTRIBUTES = frozenset(
    {
        "threadId",
        "sessionId",
        "skillId",
        "surfaceId",
        "route",
        "digest",
        "event",
        "errorName",
        "errorMessage",
        "stack",
        "componentStack",
        "durationMs",
        "attempt",
        "kind",
        "status",
        "retryable",
        "droppedCount",
        # The client's ``source`` ("window.onerror", "unhandledrejection") is
        # renamed on the way through so it can never overwrite the server's
        # ``source: "browser"`` discriminator.
        "source",
    }
)
_RENAMED = {"source": "errorSource"}

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}

log = logging.getLogger(__name__)
_browser_log = logging.getLogger(LOGGER_NAME)

router = APIRouter(tags=["client-logs"])
_CurrentUser = Annotated[User, Depends(get_current_user)]


class ClientLogEvent(BaseModel):
    """One event, field-named after the OTel LogRecord so Phase 3 is a transport swap."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(max_length=64)
    severityText: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    body: str = Field(min_length=1, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ClientLogBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ClientLogEvent]


# --- rate limiter -------------------------------------------------------------

_limiter: TokenBucketRateLimiter | None = None


def _get_limiter() -> TokenBucketRateLimiter:
    global _limiter
    if _limiter is None:
        raw = os.environ.get("CLIENT_LOG_RATE_LIMIT_PER_MIN", "")
        try:
            per_min = int(raw) if raw else DEFAULT_RATE_LIMIT_PER_MIN
        except ValueError:
            log.warning("CLIENT_LOG_RATE_LIMIT_PER_MIN=%r is not an int; using %d", raw, DEFAULT_RATE_LIMIT_PER_MIN)
            per_min = DEFAULT_RATE_LIMIT_PER_MIN
        _limiter = TokenBucketRateLimiter(capacity=max(1, per_min), refill_seconds=60.0)
    return _limiter


def reset_for_tests() -> None:
    """Drop the limiter so the next request rebuilds it from the environment."""
    global _limiter
    _limiter = None


# --- pure helpers (the testable core) ---------------------------------------


def _coerce(value: Any) -> Any:
    """A JSON-safe scalar, or a capped string rendering of anything else."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_ATTR_CHARS]
    try:
        rendered = json.dumps(value, default=str)
    except (TypeError, ValueError):
        rendered = str(value)
    return rendered[:MAX_ATTR_CHARS]


def sanitize_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Apply the allowlist. Returns ``(kept, dropped_count)``.

    A string stack field is kept whole (its cap is enforced earlier, as a
    rejection). A non-string in a stack field is not a stack; it is flattened
    and capped like any other value.
    """
    kept: dict[str, Any] = {}
    dropped = 0
    for key, value in attributes.items():
        if key not in ALLOWED_ATTRIBUTES:
            dropped += 1
            continue
        out_key = _RENAMED.get(key, key)
        if key in _STACK_FIELDS and isinstance(value, str):
            kept[out_key] = value
        else:
            kept[out_key] = _coerce(value)
    return kept, dropped


def _check_event_caps(index: int, event: ClientLogEvent) -> None:
    """Raise 413 for any event over cap. Runs for the WHOLE batch before anything is emitted."""
    attrs = event.attributes
    for field_name in _STACK_FIELDS:
        value = attrs.get(field_name)
        if isinstance(value, str) and len(value) > MAX_STACK_CHARS:
            raise HTTPException(
                status_code=413,
                detail=f"events[{index}].attributes.{field_name} is {len(value)} chars; max is {MAX_STACK_CHARS}",
            )
    without_stacks = {
        "timestamp": event.timestamp,
        "severityText": event.severityText,
        "body": event.body,
        "attributes": {k: v for k, v in attrs.items() if k not in _STACK_FIELDS},
    }
    size = len(json.dumps(without_stacks, default=str).encode("utf-8"))
    if size > MAX_EVENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"events[{index}] is {size} bytes excluding stack fields; max is {MAX_EVENT_BYTES}",
        )


def _service_context() -> dict[str, str]:
    # Read per call, not at import: this module is template content and the
    # service name is per-deployment, so it must come from the environment.
    # COMMIT_SHA is baked into the image at build time (cloudbuild.yaml passes it
    # as --build-arg), so a promoted image reports the commit test ran; a release
    # tag resolves to that commit.
    return {
        "service": os.environ.get("CLIENT_LOG_SERVICE_NAME") or "browser",
        "version": os.environ.get("COMMIT_SHA") or "dev",
    }


def build_log_record(event: ClientLogEvent, user: User) -> tuple[int, str, dict[str, Any], int]:
    """Turn one validated event into ``(level, message, json_fields, dropped_fields)``.

    ``message`` and ``severity`` are deliberately NOT in ``json_fields``:
    ``StructuredFormatter`` applies ``json_fields`` over its own payload, so a
    key there would let a client rewrite the line's severity.
    """
    attrs, dropped = sanitize_attributes(event.attributes)
    fields: dict[str, Any] = {
        "source": "browser",
        "clientTimestamp": event.timestamp,
        "event": attrs.pop("event", event.body),
        # Identity: the token's, and nothing the body said.
        "uid": user.uid,
        "email": user.email,
        "domain": user.domain,
        "groupTags": sorted(user.group_tags),
        "authMode": user.auth_mode,
    }
    fields.update(attrs)

    message = event.body
    if event.severityText == "ERROR":
        name = attrs.get("errorName")
        detail = attrs.get("errorMessage")
        stack = attrs.get("stack")
        if name or detail:
            message = f"{name or 'Error'}: {detail or ''}".rstrip(": ").rstrip()
        # Error Reporting parses frames out of ``message``; without them every
        # browser error groups into a single bucket named after the event.
        if isinstance(stack, str) and stack:
            message = f"{message}\n{stack}" if not stack.startswith(message) else stack
        # serviceContext alone marks this for Error Reporting (no @type — see the
        # module docstring: BigQuery rejects that column and the sink dropped the row).
        fields["serviceContext"] = _service_context()

    return _LEVELS[event.severityText], message, fields, dropped


# --- the route ----------------------------------------------------------------


@router.post("/api/client-logs", status_code=202)
async def ingest_client_logs(request: Request, user: _CurrentUser) -> dict[str, int]:
    """Accept a batch of browser events. Returns counts only — never content.

    Gates, in order, and ALL of them before anything is emitted:
    body bytes → JSON → shape → batch count → per-event caps → allowlist →
    per-uid rate limit → emit.
    """
    # Cheapest gate first: refuse a declared oversize body without reading it.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"body is {declared} bytes; max is {MAX_BODY_BYTES}")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"body is {len(raw)} bytes; max is {MAX_BODY_BYTES}")

    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="body is not valid JSON") from exc
    try:
        batch = ClientLogBatch.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc

    if len(batch.events) > MAX_BATCH_EVENTS:
        raise HTTPException(status_code=413, detail=f"batch has {len(batch.events)} events; max is {MAX_BATCH_EVENTS}")
    for index, event in enumerate(batch.events):
        _check_event_caps(index, event)

    limiter = _get_limiter()
    accepted = dropped = dropped_fields = 0
    for event in batch.events:
        level, message, fields, n_dropped = build_log_record(event, user)
        dropped_fields += n_dropped
        try:
            limiter.check(user.uid)
        except RateLimitExceeded:
            dropped += 1
            continue
        _browser_log.log(level, message, extra={"json_fields": fields})
        accepted += 1

    return {"accepted": accepted, "dropped": dropped, "droppedFields": dropped_fields}


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "LOGGER_NAME",
    "MAX_ATTR_CHARS",
    "MAX_BATCH_EVENTS",
    "MAX_BODY_BYTES",
    "MAX_EVENT_BYTES",
    "MAX_STACK_CHARS",
    "ClientLogBatch",
    "ClientLogEvent",
    "build_log_record",
    "reset_for_tests",
    "router",
    "sanitize_attributes",
]
