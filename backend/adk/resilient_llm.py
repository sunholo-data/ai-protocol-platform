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

from adk.model_errors import ErrorClass, ModelTurnError, SilentStallError, classify
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

MODEL_RETRY_EVENT = "MODEL_RETRY"
MODEL_FALLBACK_EVENT = "MODEL_FALLBACK"

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
    system instruction plus every text, tool call and tool result in the
    history. Never raises — a wrong estimate only moves a deadline."""
    total = 0
    try:
        instruction = getattr(getattr(llm_request, "config", None), "system_instruction", None)
        if isinstance(instruction, str):
            total += len(instruction)
        elif instruction is not None:
            total += sum(len(getattr(p, "text", None) or "") for p in getattr(instruction, "parts", None) or [])
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


def first_token_deadline_for(prompt_chars: int, stalls: int = 0) -> float | None:
    """The first-token deadline for one attempt: the configured base, grown with
    the prompt and once more after a stall, capped. ``None`` when disabled."""
    base = first_token_deadline()
    if base is None:
        return None
    deadline = base + FIRST_TOKEN_DEADLINE_PER_MCHAR_S * prompt_chars / 1_000_000
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
        if getattr(part, "text", None) or getattr(part, "function_call", None) or getattr(part, "inline_data", None):
            return True
    return False


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
    """

    chain: list[Any]
    event_sink: Any = None
    max_retries_per_model: int = 2

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
        # Every member is sent the same request, so size it once; stalls count
        # across the whole chain for the same reason.
        prompt_chars = _prompt_chars(llm_request) if stream else 0
        stalls = 0

        for idx, member in enumerate(self.chain):
            provider = _provider_of(member)
            has_next = idx + 1 < len(self.chain)

            # Cooldown bench: skip a known-bad provider — but never skip the
            # last option (a benched provider still beats no answer at all).
            if has_next and _is_benched(provider):
                next_model = getattr(self.chain[idx + 1], "model", "unknown")
                logger.info("skipping benched provider %s (%s) -> %s", provider, member.model, next_model)
                self._emit(
                    MODEL_FALLBACK_EVENT,
                    {
                        "from_model": getattr(member, "model", "unknown"),
                        "to_model": next_model,
                        "code": "MODEL_UNAVAILABLE",
                        "provider": provider,
                        "reason": "provider_cooldown",
                    },
                )
                _count("model_fallback_total", provider, "MODEL_UNAVAILABLE")
                continue

            attempt = 0
            while True:
                visible = False
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
                    _maybe_inject_fault(provider)
                    upstream = member.generate_content_async(llm_request, stream)
                    deadline = first_token_deadline_for(prompt_chars, stalls) if stream else None
                    if deadline is not None:
                        upstream = _stream_with_first_token_deadline(upstream, deadline, member_model or "unknown")
                    async for response in upstream:
                        if not visible and _has_visible_output(response):
                            visible = True
                        yield response
                    _record_success(provider)
                    return
                except Exception as exc:
                    error_class = classify(exc)
                    last_class, last_model, last_exc = error_class, getattr(member, "model", "unknown"), exc
                    if isinstance(exc, SilentStallError):
                        stalls += 1

                    if visible:
                        # Answer content already reached the user — a retry or
                        # fallback would duplicate it. Fail typed instead.
                        _record_failure(provider)
                        _count("model_error_total", provider, error_class.code)
                        raise ModelTurnError(error_class, last_model) from exc

                    if error_class.transient and attempt < self.max_retries_per_model:
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
                            },
                        )
                        _count("model_retry_total", provider, error_class.code)
                        await _async_sleep(delay)
                        continue

                    if error_class.fallbackable and has_next:
                        _record_failure(provider)
                        next_model = getattr(self.chain[idx + 1], "model", "unknown")
                        logger.warning(
                            "model %s failed (%s, %s attempts) — falling back to %s",
                            last_model,
                            error_class.code,
                            attempt + 1,
                            next_model,
                        )
                        self._emit(
                            MODEL_FALLBACK_EVENT,
                            {
                                "from_model": last_model,
                                "to_model": next_model,
                                "code": error_class.code,
                                "provider": provider,
                            },
                        )
                        _count("model_fallback_total", provider, error_class.code)
                        break  # next chain member

                    if error_class.fallbackable:
                        _record_failure(provider)
                    _count("model_error_total", provider, error_class.code)
                    raise ModelTurnError(error_class, last_model) from exc

        # Chain exhausted via cooldown-skips and/or fallbacks.
        if last_class is not None:
            raise ModelTurnError(last_class, last_model) from last_exc
        raise ModelTurnError(  # pragma: no cover — every member benched, none ran
            ErrorClass(transient=False, fallbackable=False, code="MODEL_UNAVAILABLE", message="all providers benched"),
            last_model,
        )
