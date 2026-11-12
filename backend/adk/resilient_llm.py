"""MODEL-RELIABILITY M3 — ``ResilientLlm``: retry + fallback on the BaseLlm seam.

Wraps an ordered chain of resolved ``BaseLlm`` instances (primary first).
On classified failure (``adk.model_errors``) it retries transient errors
with capped full-jitter backoff, then moves down the chain — but ONLY
while no *visible* output (non-thought content) has reached the consumer;
after that, re-running the turn would duplicate user-visible output, so
it raises a typed ``ModelTurnError`` instead. Every decision emits a
reliability event (transient ``MODEL_RETRY`` / persistent
``MODEL_FALLBACK``) through the event sink, which production wires to
the per-request LatencyTracker queue that ``stream_agui_events`` drains
onto the SSE stream.

Why here and not LiteLLM's native ``fallbacks=`` / per-client retries:
this is the single place all three providers AND the AG-UI notification
contract meet (design doc "Standards Compliance"). Retries live in ONE
layer by design — do not add ``num_retries``/``HttpRetryOptions`` to
chain members, or attempts multiply and blow the <30s failover budget.

Streaming contract: yielded ``LlmResponse`` objects pass through
untouched — same objects, same order. The wrapper must stay invisible to
ag_ui_adk's translator.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any

from google.adk.models.base_llm import BaseLlm, LlmCapabilities

from adk.model_errors import (
    EmptyModelResponseError,
    ErrorClass,
    MalformedFunctionCallError,
    ModelTurnError,
    SilentStallError,
    classify,
)
from adk.schema_conformance import sanitize_function_declarations

logger = logging.getLogger(__name__)

# Patchable seam for tests (real backoff sleeps would slow the suite).
_async_sleep = asyncio.sleep

BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0

# Provider cooldown: after this many CONSECUTIVE abandoned turns on a
# provider, skip it as primary for the window — later turns go straight to
# the fallback instead of re-paying retries + request timeouts during a
# sustained outage. Per-instance (module) state: Cloud Run instances learn
# independently, which is acceptable at current scale (design doc Open
# Question tracks a shared-store upgrade path).
COOLDOWN_THRESHOLD = 3
COOLDOWN_SECONDS = 120.0

#: Seconds a STREAMED request may stay silent before it is abandoned and
#: retried as a ``SilentStallError``. Env ``MODEL_FIRST_TOKEN_DEADLINE_S``
#: overrides; ``0`` disables. 10 s is the measured gap in a bimodal tail
#: (flash-lite on Vertex: 108/114 turns under 6.1 s, then 14.9 s and up),
#: and leaves the retry room to land before a 30 s client hang-timeout.
#: Non-streamed calls are never bounded: their single chunk IS the answer.
FIRST_TOKEN_DEADLINE_ENV = "MODEL_FIRST_TOKEN_DEADLINE_S"
FIRST_TOKEN_DEADLINE_S = 10.0

#: That 10 s was measured on SMALL prompts, and prefill grows with the prompt.
#: Probed 2026-10-21 (gemini-3.8-flash, global, dynamic thinking with
#: include_thoughts, n=2 per size) — first chunk 2.9-3.5 s at 40K chars,
#: 4.2-5.4 s at 400K, 9.6-15.4 s at 1.2M, 15.2-23.5 s at 2.4M. A flat 10 s
#: killed a document-heavy specialist's turns on a deployed fork's prod whose
#: long history put the first chunk
#: at ~10-12 s, on EVERY member of the chain, three attempts each (2026-10-16
#: and 10-21). So the deadline grows by this many seconds per million prompt
#: characters...
FIRST_TOKEN_DEADLINE_PER_MCHAR_S = 10.0
#: ...doubles once after a stall in the same turn (a request that needs 11 s
#: never fits in 10 s however often it is re-sent, and the size estimate can't
#: see everything)...
FIRST_TOKEN_STALL_ESCALATION = 2.0
#: ...and never exceeds this. Genuine stalls ran 48-105 s, and the AG-UI
#: heartbeat keeps the client stream alive meanwhile.
FIRST_TOKEN_DEADLINE_MAX_S = 60.0

#: Those numbers are Gemini's. Claude (direct API, via LiteLLM) reads a long
#: prompt more slowly: on Sunholo 2026-11-09, claude-haiku-5-5 took 6.7-8.0 s to
#: its first chunk at 165-176K chars uncached, and the Gemini-sized 11.7 s
#: deadline called a slightly slower call a stall. The turn then fell back to
#: Gemini mid tool loop and died. Providers other than Gemini get more base
#: time and more per million characters (prompt caching shortens the real wait
#: on every later call of a turn).
SLOW_PREFILL_PROVIDERS: dict[str, tuple[float, float]] = {
    # provider: (extra base seconds, seconds per million prompt characters)
    "anthropic": (5.0, 60.0),
    "openai": (5.0, 60.0),
}

#: Gemini 3 refuses a history whose function calls carry no thought signature
#: ("Function call is missing a thought_signature"), and calls made by ANOTHER
#: provider never have one. Google documents this value as the way to pass
#: such calls through (https://ai.google.dev/gemini-api/docs/thought-signatures).
FOREIGN_CALL_THOUGHT_SIGNATURE = b"skip_thought_signature_validator"

MODEL_RETRY_EVENT = "MODEL_RETRY"
MODEL_FALLBACK_EVENT = "MODEL_FALLBACK"
#: A request is about to be sent to one chain member. Lets the Activity feed say
#: which model the turn is waiting on, and for how long, instead of a silent gap
#: between tool calls (a 2026-10-30 tool-heavy turn spent ~6 min across 15 of these).
MODEL_CALL_EVENT = "MODEL_CALL"

_provider_health: dict[str, dict[str, float]] = {}


def reset_provider_health() -> None:
    """Test hook — clear the module-level cooldown registry."""
    _provider_health.clear()


def _record_failure(provider: str) -> None:
    entry = _provider_health.setdefault(provider, {"failures": 0, "benched_until": 0.0})
    entry["failures"] += 1
    if entry["failures"] >= COOLDOWN_THRESHOLD:
        entry["benched_until"] = time.monotonic() + COOLDOWN_SECONDS
        logger.warning(
            "provider %s benched for %ss after %s consecutive failures", provider, COOLDOWN_SECONDS, entry["failures"]
        )


def _record_success(provider: str) -> None:
    _provider_health.pop(provider, None)


def _is_benched(provider: str) -> bool:
    entry = _provider_health.get(provider)
    return bool(entry) and time.monotonic() < entry["benched_until"]


def _provider_of(model: Any) -> str:
    """Best-effort provider label for cooldown + events (never raises)."""
    hint = getattr(model, "provider_hint", None)
    if isinstance(hint, str) and hint:
        return hint
    try:
        from google.adk.models.google_llm import Gemini

        if isinstance(model, Gemini):
            return "gemini"
    except ImportError:  # pragma: no cover
        pass
    model_name = getattr(model, "model", "") or ""
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    if model_name.startswith("gemini"):
        return "gemini"
    return type(model).__name__.lower()


def first_token_deadline() -> float | None:
    """The configured first-token deadline in seconds, or ``None`` when disabled."""
    raw = (os.environ.get(FIRST_TOKEN_DEADLINE_ENV) or "").strip()
    if not raw:
        return FIRST_TOKEN_DEADLINE_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using %.0fs", FIRST_TOKEN_DEADLINE_ENV, raw, FIRST_TOKEN_DEADLINE_S)
        return FIRST_TOKEN_DEADLINE_S
    return value if value > 0 else None


def _prompt_chars(llm_request: Any) -> int:
    """Rough size of what the model must read before it can say anything: the
    system instruction, tool declarations and every text, tool call and tool result in the
    history. Never raises — a wrong estimate only moves a deadline."""
    total = 0
    try:
        instruction = getattr(getattr(llm_request, "config", None), "system_instruction", None)
        if isinstance(instruction, str):
            total += len(instruction)
        elif instruction is not None:
            total += sum(len(getattr(p, "text", None) or "") for p in getattr(instruction, "parts", None) or [])
        for tool in getattr(getattr(llm_request, "config", None), "tools", None) or []:
            total += len(tool.model_dump_json(exclude_none=True) if hasattr(tool, "model_dump_json") else str(tool))
        for content in getattr(llm_request, "contents", None) or []:
            for part in getattr(content, "parts", None) or []:
                total += len(getattr(part, "text", None) or "")
                call = getattr(part, "function_call", None)
                if call is not None:
                    total += len(str(getattr(call, "args", None) or ""))
                result = getattr(part, "function_response", None)
                if result is not None:
                    total += len(str(getattr(result, "response", None) or ""))
    except Exception as exc:
        logger.debug("first-token: prompt size estimate failed (%r) — using what was counted", exc)
    return total


def first_token_deadline_for(prompt_chars: int, stalls: int = 0, provider: str = "") -> float | None:
    """The first-token deadline for one attempt: the configured base, grown with
    the prompt (faster for providers that read slowly) and once more after a
    stall, capped. ``None`` when disabled."""
    base = first_token_deadline()
    if base is None:
        return None
    extra, per_mchar = SLOW_PREFILL_PROVIDERS.get(provider, (0.0, FIRST_TOKEN_DEADLINE_PER_MCHAR_S))
    deadline = base + extra + per_mchar * prompt_chars / 1_000_000
    if stalls:
        deadline *= FIRST_TOKEN_STALL_ESCALATION
    return min(deadline, max(base, FIRST_TOKEN_DEADLINE_MAX_S))


async def _stream_with_first_token_deadline(stream: Any, deadline_s: float, model: str):
    """Yield from ``stream``; if its FIRST item takes longer than ``deadline_s``,
    close it and raise ``SilentStallError``. Once anything has arrived the
    stream runs to completion however long the rest takes — a slow tail is
    not a stall."""
    iterator = stream.__aiter__()
    try:
        first = await asyncio.wait_for(iterator.__anext__(), timeout=deadline_s)
    except StopAsyncIteration:
        return
    except TimeoutError:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except BaseException as exc:  # a dead stream may object to closing; irrelevant now
                logger.debug("first-token: aclose after timeout raised %r (ignored)", exc)
        raise SilentStallError(deadline_s, model) from None
    yield first
    async for item in iterator:
        yield item


def _backoff_delay(attempt: int) -> float:
    """Full jitter, capped; floor keeps 'slept a positive amount' observable."""
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
    return max(0.05, random.uniform(0, ceiling))


def _has_visible_output(response: Any) -> bool:
    """True when any non-thought content part would render as answer output
    (text, tool call, inline data). Thought parts feed the ThinkingPanel
    only — repeating a thinking phase on retry/fallback is acceptable;
    repeating answer text or re-issuing a tool call is not."""
    content = getattr(response, "content", None)
    parts = getattr(content, "parts", None) or []
    for part in parts:
        if getattr(part, "thought", False):
            continue
        if any(getattr(part, field, None) for field in ("text", "function_call", "inline_data", "file_data")):
            return True
    return False


#: Structured log event written once per chain-member attempt (see ``_CallLog``).
MODEL_CALL_LOG_EVENT = "model_call"

_USAGE_FIELDS = (
    ("prompt_token_count", "prompt_tokens"),
    ("candidates_token_count", "output_tokens"),
    ("thoughts_token_count", "thinking_tokens"),
    ("cached_content_token_count", "cached_tokens"),
)


class _CallLog:
    """Latency record for ONE attempt on ONE chain member.

    The turn-level ``ttft`` line cannot answer "how fast is model X to its first
    token": its ``first_model_token_ms`` is the first TEXT reaching the wire, so on
    a tool-using turn it includes every tool round-trip, and it names only the
    model that answered last. Retries and silent stalls on the way there were
    visible only as a UI event and an OTel counter. This writes one
    ``event="model_call"`` line per attempt instead: model, provider, region,
    time to the first chunk of any kind, time to the first visible (answer or
    tool-call) chunk, duration, token usage and how the attempt ended.

    Carries model ids, timings, counts and error codes only — never prompt or
    response content. Fail-open: logging must never break a turn.
    """

    __slots__ = ("_done", "_fields", "_first_chunk", "_first_visible", "_record", "_started", "_tracker", "_usage")

    def __init__(self, tracker: Any, record: bool, **fields: Any) -> None:
        # ``tracker`` supplies the skill/session ids; ``record`` says whether this
        # attempt is the TURN's work (counted on the ttft line) or housekeeping.
        self._tracker = tracker
        self._record = record
        self._fields = fields
        self._started = time.monotonic()
        self._first_chunk: float | None = None
        self._first_visible: float | None = None
        self._usage: Any = None
        self._done = False

    def restart(self) -> None:
        """Reset the clock to the moment the request is actually sent."""
        self._started = time.monotonic()

    def chunk(self, response: Any, visible: bool) -> None:
        now = time.monotonic()
        if self._first_chunk is None:
            self._first_chunk = now
        if visible and self._first_visible is None:
            self._first_visible = now
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self._usage = usage

    def finish(self, outcome: str, code: str = "", action: str = "") -> None:
        if self._done:
            return
        self._done = True
        # The consumer stopping AFTER the answer reached it is normal, not a
        # failure: ADK closes the stream once a transfer_to_agent call is made,
        # so every delegated turn would otherwise log its router as cancelled.
        if outcome == "cancelled" and self._first_visible is not None:
            outcome = "closed"
        try:
            now = time.monotonic()

            def ms(t: float | None) -> float | None:
                return round((t - self._started) * 1000.0, 1) if t is not None else None

            payload: dict[str, Any] = {
                "event": MODEL_CALL_LOG_EVENT,
                **self._fields,
                "outcome": outcome,
                "first_chunk_ms": ms(self._first_chunk),
                "first_visible_ms": ms(self._first_visible),
                "duration_ms": ms(now),
            }
            if code:
                payload["code"] = code
            if action:
                payload["action"] = action
            for src, dst in _USAGE_FIELDS:
                value = getattr(self._usage, src, None) if self._usage is not None else None
                if isinstance(value, int):
                    payload[dst] = value
            if self._tracker is not None:
                payload.setdefault("skill_id", getattr(self._tracker, "skill_id", ""))
                payload.setdefault("session_id", getattr(self._tracker, "session_id", ""))
            logger.info(
                "model_call model=%s outcome=%s first_chunk_ms=%s duration_ms=%s",
                payload.get("model"),
                outcome,
                payload["first_chunk_ms"],
                payload["duration_ms"],
                extra={"json_fields": payload},
            )
            if self._record and self._tracker is not None:
                self._tracker.record_model_call(
                    model=str(payload.get("model") or ""), outcome=outcome, code=code, action=action
                )
        except Exception as exc:  # fail-open
            logger.warning("model_call log failed (suppressed): %s", exc)


def _check_generated_tool_call(response: Any) -> None:
    # Gemini's soft failure is an LlmResponse, not an exception. Passing it to
    # ADK ends the run without a reply. Regenerate before any visible output.
    if getattr(response, "error_code", None) == "MALFORMED_FUNCTION_CALL":
        raise MalformedFunctionCallError("The model generated an invalid tool call")


def _tool_call_repair_request(request: Any) -> Any:
    """A retry-local hint: do not mutate the agent's shared generation config."""
    from google.adk.models.llm_request import LlmRequest

    if not isinstance(request, LlmRequest):
        return request
    config = request.config.model_copy(deep=True)
    hint = (
        "Your previous generation was rejected as MALFORMED_FUNCTION_CALL. "
        "Generate a fresh valid tool call using exactly the declared parameter names, nesting and types. "
        "Do not output Python code or a function call as text."
    )
    instruction = config.system_instruction
    if isinstance(instruction, str):
        config.system_instruction = instruction + "\n\n" + hint
    elif isinstance(instruction, list):
        config.system_instruction = [*instruction, hint]
    else:
        from google.genai import types

        if instruction is not None and not isinstance(instruction, types.Content | types.Part):
            return request  # preserve an unfamiliar instruction representation
        parts = (
            [instruction] if isinstance(instruction, types.Part) else list(getattr(instruction, "parts", None) or [])
        )
        parts.append(types.Part.from_text(text=hint))
        config.system_instruction = types.Content(parts=parts)
    return request.model_copy(update={"config": config})


async def _close_generated_error_stream(stream: Any) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception as exc:
            logger.debug("tool-call recovery: stream close failed: %s", exc)


# --- Cross-provider tool-history sanitizer (v6.13.0) --------------------------
# The three providers model tool exchanges differently and their ids don't
# round-trip: Anthropic (tool_use/tool_result) and OpenAI (tool_call_id) REQUIRE
# a matched id linking a call to its result; Gemini's functionCall/Response have
# NONE (matched by name/order). ADK's LiteLlm converter reads
# `tool_call_id = part.function_call.id or ""`, so once a Gemini fallback emits a
# tool call and its result enters the history, a later Anthropic/OpenAI call sees
# an EMPTY id and raises `AnthropicException: 'tool_call_id'`. Backfilling ids +
# matching them (and dropping orphan responses) makes the history valid for any
# target. Idempotent; a no-op when there are no tool parts or all are already
# id'd (a native Anthropic/OpenAI history we must not disturb).


def _parts_of(content: Any) -> list:
    return list(getattr(content, "parts", None) or [])


def sanitize_cross_provider_tool_history(llm_request: Any) -> bool:
    """Backfill functionCall ids + matching functionResponse ids and drop orphan
    tool-response parts so a tool history validates across providers. Mutates
    ``llm_request.contents`` in place. Returns True if it changed anything."""
    contents = getattr(llm_request, "contents", None)
    if not contents:
        return False
    calls = [p.function_call for c in contents for p in _parts_of(c) if getattr(p, "function_call", None) is not None]
    resps = [
        p.function_response for c in contents for p in _parts_of(c) if getattr(p, "function_response", None) is not None
    ]
    if not calls and not resps:
        return False
    # Already fully id'd (a native Anthropic/OpenAI history) — don't touch it.
    if all(getattr(fc, "id", None) for fc in calls) and all(getattr(fr, "id", None) for fr in resps):
        return False

    changed = False
    counter = 0
    pending: dict[str, list[str]] = {}  # tool name -> FIFO of unanswered call ids
    for content in contents:
        kept = []
        for part in _parts_of(content):
            fc = getattr(part, "function_call", None)
            fr = getattr(part, "function_response", None)
            if fc is not None:
                if not getattr(fc, "id", None):
                    fc.id = f"call_{counter}"
                    changed = True
                counter += 1
                pending.setdefault(fc.name, []).append(fc.id)
                kept.append(part)
            elif fr is not None:
                ids = pending.get(getattr(fr, "name", None) or "")
                if ids:
                    matched = ids.pop(0)
                    if getattr(fr, "id", None) != matched:
                        fr.id = matched
                        changed = True
                    kept.append(part)
                else:
                    # Orphan response (no matching call) — Anthropic rejects it.
                    changed = True
            else:
                kept.append(part)
        if len(kept) != len(_parts_of(content)):
            content.parts = kept
    return changed


def backfill_foreign_thought_signatures(llm_request: Any) -> bool:
    """Give every function call that has no thought signature (one another
    provider made) Google's pass-through signature, so a Gemini 3 fallback can
    continue a turn that already used tools. Mutates in place; True if changed.
    Only for a Gemini member: other providers neither need nor expect it."""
    changed = False
    for content in getattr(llm_request, "contents", None) or []:
        for part in _parts_of(content):
            if getattr(part, "function_call", None) is not None and not getattr(part, "thought_signature", None):
                part.thought_signature = FOREIGN_CALL_THOUGHT_SIGNATURE
                changed = True
    return changed


# --- Fault injection (M4, local-only) -----------------------------------------
# FAULT_INJECT_MODEL="provider:status:count" makes the first `count` attempts
# against `provider` raise a mapped exception INSTEAD of calling the model —
# `make probe-fallback` uses it to demonstrate the full retry->fallback->
# notice path end-to-end without breaking a real provider. Refuses to arm on
# Cloud Run (K_SERVICE) — this is a laptop tool, never a deployed behavior.

_fault_used = {"n": 0}


def reset_fault_injection() -> None:
    _fault_used["n"] = 0


def _maybe_inject_fault(provider: str) -> None:
    spec = os.environ.get("FAULT_INJECT_MODEL", "").strip()
    if not spec or os.environ.get("K_SERVICE"):
        return
    try:
        target_provider, status_s, count_s = spec.split(":")
        status, count = int(status_s), int(count_s)
    except ValueError:
        logger.warning("FAULT_INJECT_MODEL=%r malformed (want provider:status:count) — ignoring", spec)
        return
    if provider != target_provider or _fault_used["n"] >= count:
        return
    _fault_used["n"] += 1
    import litellm

    logger.warning("FAULT INJECTION: raising %s for provider %s (%s/%s)", status, provider, _fault_used["n"], count)
    common = {"llm_provider": provider, "model": f"{provider}/fault-injected"}
    if status == 429:
        raise litellm.RateLimitError("fault-injected rate limit", **common)
    if status == 503:
        raise litellm.ServiceUnavailableError("fault-injected unavailable", **common)
    if status == 401:
        raise litellm.AuthenticationError("fault-injected auth failure", **common)
    raise litellm.InternalServerError(f"fault-injected {status}", **common)


# --- OTel counters (M4) ---------------------------------------------------------
# Fail-open lazy init: reliability accounting must never break a turn.

_counters: dict[str, Any] = {}


def _count(name: str, provider: str, code: str) -> None:
    try:
        if name not in _counters:
            from opentelemetry import metrics

            meter = metrics.get_meter("aitana.model_reliability")
            _counters[name] = meter.create_counter(name)
        _counters[name].add(1, {"provider": provider, "code": code})
    except Exception as exc:  # pragma: no cover
        logger.debug("otel counter %s failed (suppressed): %s", name, exc)


class ResilientLlm(BaseLlm):
    """Retry/fallback wrapper over an ordered chain of ``BaseLlm`` models.

    ``chain[0]`` is the primary; an empty-fallback (length-1) chain behaves
    exactly like the bare model plus classification-on-error. ``event_sink``
    is a ``(name: str, value: dict) -> None`` callable; ``None`` wires to
    the current request's LatencyTracker.

    ``reports_model`` marks a chain whose calls ARE the turn: it announces each
    call (``MODEL_CALL``) and records the member that answered as the turn's
    model. Housekeeping chains (the compaction summariser) turn it off so they
    neither appear as the turn's work nor relabel the turn's model.
    """

    chain: list[Any]
    event_sink: Any = None
    max_retries_per_model: int = 2
    reports_model: bool = True

    def __init__(self, chain: list[Any], event_sink: Any = None, max_retries_per_model: int = 2, **kwargs: Any) -> None:
        if not chain:
            raise ValueError("ResilientLlm requires a non-empty chain")
        super().__init__(
            model=getattr(chain[0], "model", "resilient"),
            chain=chain,
            event_sink=event_sink,
            max_retries_per_model=max_retries_per_model,
            **kwargs,
        )

    @property
    def capabilities(self) -> LlmCapabilities:
        # ADK builds the request before retry/fallback chooses a member. Only
        # advertise schema+tools when EVERY possible recipient supports it;
        # a union (or the primary alone) can send an invalid request to fallback.
        return LlmCapabilities(
            output_schema_and_tools=all(
                getattr(getattr(member, "capabilities", None), "output_schema_and_tools", False)
                for member in self.chain
            )
        )

    # -- events ---------------------------------------------------------------

    def _emit(self, name: str, value: dict) -> None:
        try:
            if self.event_sink is not None:
                self.event_sink(name, value)
                return
            from observability.timing import get_current_tracker

            get_current_tracker().emit_reliability_event(name, value)
        except Exception as exc:  # fail-open: signaling must never break the turn
            logger.warning("reliability event emit failed (suppressed): %s", exc)

    def _tracker(self) -> Any:
        """The request's tracker for PROGRESS signals, or None when this chain
        doesn't speak for the turn. Unlike ``_emit`` these never go to
        ``event_sink``: the sink carries reliability events (retry/fallback) only.
        """
        if not self.reports_model or self.event_sink is not None:
            return None
        from observability.timing import get_current_tracker

        return get_current_tracker()

    def _announce_call(self, model: str, provider: str, attempt: int, deadline: float | None) -> None:
        try:
            tracker = self._tracker()
            if tracker is not None:
                tracker.emit_reliability_event(
                    MODEL_CALL_EVENT,
                    {
                        "model": model,
                        "provider": provider,
                        "attempt": attempt,
                        "deadline_s": round(deadline, 1) if deadline is not None else None,
                    },
                )
        except Exception as exc:  # fail-open: signaling must never break the turn
            logger.warning("model-call announce failed (suppressed): %s", exc)

    def _note_answered(self, model: str) -> None:
        """Record the member that produced output as the turn's model.

        The tracker is stamped once, at agent creation, with the ROOT skill's
        model, so a turn answered by a transferred specialist or a fallback
        member was logged (and shown in the Activity header) as the wrong model.
        """
        try:
            tracker = self._tracker()
            if tracker is not None and model:
                tracker.note_model_answered(model)
        except Exception as exc:  # fail-open: signaling must never break the turn
            logger.warning("answered-model note failed (suppressed): %s", exc)

    def _call_log(self, member: Any, provider: str, idx: int, attempt: int, stream: bool, **extra: Any) -> _CallLog:
        try:
            from observability.timing import get_current_tracker

            tracker = get_current_tracker()
        except Exception:  # fail-open
            tracker = None
        return _CallLog(
            tracker,
            record=self.reports_model and self.event_sink is None,
            model=getattr(member, "model", None) or "unknown",
            provider=provider,
            location=getattr(member, "location", "") or "",
            chain_index=idx,
            attempt=attempt,
            stream=stream,
            role="turn" if self.reports_model else "housekeeping",
            **extra,
        )

    def _mark_first_output(self) -> None:
        """Mark the turn's first visible model output (answer text OR a tool call).

        ``first_model_token`` only fires on TEXT, so on a tool-using turn it
        measures the whole tool loop. This is the model's real time-to-first-token
        for the turn's first answered call."""
        try:
            tracker = self._tracker()
            if tracker is not None:
                from observability.timing import STAGE_FIRST_MODEL_OUTPUT

                tracker.mark(STAGE_FIRST_MODEL_OUTPUT)
        except Exception as exc:  # fail-open
            logger.warning("first-output mark failed (suppressed): %s", exc)

    # -- core -----------------------------------------------------------------

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        # Gemini's FunctionDeclaration proto rejects `additional_properties`,
        # which ADK emits for any `dict[str, Any]` tool param. Vertex ignores
        # it; Express Mode 400s the whole request. Sanitize HERE because this
        # is the single seam every agent model call passes through — a fix in
        # one tool's signature would leave the trap armed for the next author.
        # See adk/schema_conformance.py.
        sanitize_function_declarations(llm_request)

        last_class: ErrorClass | None = None
        last_model = self.model
        last_exc: Exception | None = None
        # Every member is sent the same request, so size it once. Deadline
        # escalation belongs to retries of one endpoint, not its healthy sibling.
        prompt_chars = _prompt_chars(llm_request) if stream else 0

        for idx, member in enumerate(self.chain):
            provider = _provider_of(member)
            # A healthy sibling model/region must not erase a repeatedly
            # stalled endpoint's cooldown (e.g. flash 3.7 rescuing flash 3.8).
            endpoint = f"{provider}:{member.model}:{getattr(member, 'location', '')}"
            has_next = idx + 1 < len(self.chain)
            stalls = 0

            # Cooldown bench: skip a known-bad provider — but never skip the
            # last option (a benched provider still beats no answer at all).
            if has_next and (_is_benched(provider) or _is_benched(endpoint)):
                next_model = getattr(self.chain[idx + 1], "model", "unknown")
                logger.info("skipping benched provider %s (%s) -> %s", provider, member.model, next_model)
                self._call_log(member, provider, idx, 0, stream).finish(
                    "skipped", "provider_cooldown" if _is_benched(provider) else "model_cooldown", "fallback"
                )
                self._emit(
                    MODEL_FALLBACK_EVENT,
                    {
                        "from_model": getattr(member, "model", "unknown"),
                        "to_model": next_model,
                        "code": "MODEL_UNAVAILABLE",
                        "provider": provider,
                        "reason": "provider_cooldown" if _is_benched(provider) else "model_cooldown",
                    },
                )
                _count("model_fallback_total", provider, "MODEL_UNAVAILABLE")
                continue

            attempt = 0
            while True:
                visible = False
                started = time.monotonic()
                call_log = self._call_log(member, provider, idx, attempt, stream, prompt_chars=prompt_chars)
                try:
                    # Make the tool history valid for whatever provider this
                    # member is (v6.13.0). No-op for a native fully-id'd history
                    # or a no-tool turn; on a cross-provider hop it backfills the
                    # id-less Gemini functionCall/Response ids so the next
                    # Anthropic/OpenAI call doesn't die on 'tool_call_id'.
                    try:
                        if sanitize_cross_provider_tool_history(llm_request):
                            logger.info(
                                "cross-provider fallback: sanitized tool-history ids for %s",
                                getattr(member, "model", "unknown"),
                            )
                    except Exception as exc:  # never break the turn on sanitation
                        logger.warning("cross-provider tool-history sanitize failed (suppressed): %s", exc)
                    if provider == "gemini":
                        try:
                            if backfill_foreign_thought_signatures(llm_request):
                                logger.info(
                                    "cross-provider fallback: pass-through thought signatures for %s",
                                    getattr(member, "model", "unknown"),
                                )
                        except Exception as exc:  # never break the turn on sanitation
                            logger.warning("thought-signature backfill failed (suppressed): %s", exc)
                    # Each member expects llm_request.model to name ITS OWN model.
                    # ADK stamps llm_request.model ONCE with the chain's primary
                    # (e.g. claude-opus-4-8); a fallback member left with that id
                    # is mis-called — a Gemini fallback handed "claude-opus-4-8"
                    # 404s on Vertex's anthropic Model-Garden publisher path
                    # (ROOT CAUSE of the 2026-07-16 fallback failures: every
                    # fallback target 404'd because the id was never rewritten).
                    member_model = getattr(member, "model", None)
                    if member_model:
                        try:
                            llm_request.model = member_model
                        except Exception:  # best effort — never break the turn on this
                            pass
                    deadline = first_token_deadline_for(prompt_chars, stalls, provider) if stream else None
                    self._announce_call(member_model or "unknown", provider, attempt, deadline)
                    started = time.monotonic()
                    call_log.restart()
                    _maybe_inject_fault(provider)
                    member_stream = member.generate_content_async(llm_request, stream)
                    upstream = member_stream
                    if deadline is not None:
                        upstream = _stream_with_first_token_deadline(upstream, deadline, member_model or "unknown")
                    async for response in upstream:
                        # Before the explicit-stop pass-through below: this
                        # error_code is a recoverable bad draw, not a refusal.
                        _check_generated_tool_call(response)
                        finish = getattr(response, "finish_reason", None)
                        explicit_stop = finish and getattr(finish, "value", finish) not in (
                            "STOP",
                            "FINISH_REASON_UNSPECIFIED",
                        )
                        if (
                            explicit_stop
                            or getattr(response, "error_code", None)
                            or getattr(response, "interrupted", False)
                        ):
                            # Preserve explicit refusals, safety blocks and user
                            # interruption. They are not empty successes and
                            # must not be bypassed by another provider.
                            call_log.chunk(response, False)
                            stop_code = str(
                                getattr(response, "error_code", None) or getattr(finish, "value", finish) or ""
                            )
                            call_log.finish(
                                "interrupted" if getattr(response, "interrupted", False) else "stopped", stop_code
                            )
                            yield response
                            return
                        response_visible = _has_visible_output(response)
                        call_log.chunk(response, response_visible)
                        if not visible and response_visible:
                            visible = True
                            self._note_answered(member_model or "")
                            self._mark_first_output()
                        # An empty terminal chunk would tell ADK to finish the
                        # turn before fallback can rescue it. Thought partials
                        # still stream; successful answer/tool chunks pass intact.
                        if visible or getattr(response, "partial", False):
                            yield response
                    if not visible:
                        raise EmptyModelResponseError(f"{member_model} completed without answer content or tool calls")
                    _record_success(provider)
                    _record_success(endpoint)
                    call_log.finish("ok")
                    return
                except Exception as exc:
                    if isinstance(exc, MalformedFunctionCallError):
                        await _close_generated_error_stream(upstream)
                        if upstream is not member_stream:
                            await _close_generated_error_stream(member_stream)
                    error_class = classify(exc)
                    last_class, last_model, last_exc = error_class, getattr(member, "model", "unknown"), exc
                    if isinstance(exc, SilentStallError):
                        stalls += 1
                    waited_s = round(time.monotonic() - started, 1)

                    if visible:
                        # Answer content already reached the user — a retry or
                        # fallback would duplicate it. Fail typed instead.
                        _record_failure(provider)
                        _count("model_error_total", provider, error_class.code)
                        call_log.finish("error", error_class.code, "raise")
                        raise ModelTurnError(error_class, last_model) from exc

                    # A silent attempt has already spent its entire deadline.
                    # Prefer an alternative from the SAME provider immediately.
                    # Another provider is a worse rescue (it reads the tool
                    # history another model wrote, and its own prefill starts
                    # from cold), so first retry this endpoint once with the
                    # doubled deadline; only a second stall moves on.
                    next_provider = _provider_of(self.chain[idx + 1]) if has_next else ""
                    stalled_with_alternative = (
                        isinstance(exc, SilentStallError) and has_next and (next_provider == provider or stalls >= 2)
                    )
                    retry_allowed = (
                        error_class.transient and attempt < self.max_retries_per_model and not stalled_with_alternative
                    )
                    if isinstance(exc, MalformedFunctionCallError) and attempt >= 1:
                        retry_allowed = False
                    if retry_allowed:
                        call_log.finish("error", error_class.code, "retry")
                        if isinstance(exc, MalformedFunctionCallError):
                            llm_request = _tool_call_repair_request(llm_request)
                        attempt += 1
                        delay = error_class.retry_after if error_class.retry_after else _backoff_delay(attempt)
                        self._emit(
                            MODEL_RETRY_EVENT,
                            {
                                "model": last_model,
                                "attempt": attempt,
                                "delay_s": round(delay, 2),
                                "code": error_class.code,
                                "provider": provider,
                                "waited_s": waited_s,
                            },
                        )
                        _count("model_retry_total", provider, error_class.code)
                        await _async_sleep(delay)
                        continue

                    if error_class.fallbackable and has_next:
                        call_log.finish("error", error_class.code, "fallback")
                        _record_failure(
                            endpoint if isinstance(exc, SilentStallError | EmptyModelResponseError) else provider
                        )
                        next_model = getattr(self.chain[idx + 1], "model", "unknown")
                        logger.warning(
                            "model %s failed (%s, %s attempts, %.1fs) — falling back to %s",
                            last_model,
                            error_class.code,
                            attempt + 1,
                            waited_s,
                            next_model,
                        )
                        self._emit(
                            MODEL_FALLBACK_EVENT,
                            {
                                "from_model": last_model,
                                "to_model": next_model,
                                "code": error_class.code,
                                "provider": provider,
                                "waited_s": waited_s,
                            },
                        )
                        _count("model_fallback_total", provider, error_class.code)
                        break  # next chain member

                    if error_class.fallbackable:
                        _record_failure(provider)
                    _count("model_error_total", provider, error_class.code)
                    call_log.finish("error", error_class.code, "raise")
                    raise ModelTurnError(error_class, last_model) from exc
                finally:
                    # Consumer closed the stream (client disconnect, cancellation,
                    # or a transfer after the call) or a BaseException escaped:
                    # still account for the attempt ("closed" once output arrived).
                    call_log.finish("cancelled")

        # Chain exhausted via cooldown-skips and/or fallbacks.
        if last_class is not None:
            raise ModelTurnError(last_class, last_model) from last_exc
        raise ModelTurnError(  # pragma: no cover — every member benched, none ran
            ErrorClass(transient=False, fallbackable=False, code="MODEL_UNAVAILABLE", message="all providers benched"),
            last_model,
        )
