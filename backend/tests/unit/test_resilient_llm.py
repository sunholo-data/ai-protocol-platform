"""MODEL-RELIABILITY M3 — ``ResilientLlm`` retry/fallback wrapper.

The wrapper sits on ADK's ``BaseLlm.generate_content_async`` seam — the
one streaming interface all three providers share. Its contract, pinned
here with scripted fakes:

1. **Streaming passthrough is sacred**: yielded ``LlmResponse`` objects
   pass through IDENTICALLY (same objects, same order) — partials,
   thought parts, everything. Any transformation would corrupt the
   ag_ui_adk translation downstream.
2. **Retry only what's transient, only before visible output.** Backoff
   between attempts (full jitter, capped), honoring provider retry-after.
3. **Fall back only what's fallbackable, only before visible output.**
   Visible = any non-thought content part reached the consumer; after
   that, re-running the turn would duplicate user-visible output, so we
   fail with a typed error instead. Thought-only output does NOT count
   as visible (a dead model turn beats a dead chat; a repeated thinking
   phase is acceptable).
4. **Every decision emits an event** (MODEL_RETRY transient /
   MODEL_FALLBACK persistent) through the injected sink.
5. **Provider cooldown**: N consecutive fallbackable failures put the
   provider on the bench for a window — later turns skip straight to
   the fallback instead of re-paying the retry+timeout tax.
6. **Chain exhausted → ModelTurnError** carrying the LAST failure's
   classification (skill_processor renders it as a typed RUN_ERROR).
"""

from __future__ import annotations

import asyncio
from typing import Any

import litellm
import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adk import resilient_llm as rl
from adk.model_errors import ModelTurnError
from adk.resilient_llm import ResilientLlm

# --- Scripted fake ----------------------------------------------------------


def _text(text: str, *, partial: bool = True) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), partial=partial)


def _thought(text: str) -> LlmResponse:
    part = types.Part(text=text)
    part.thought = True
    return LlmResponse(content=types.Content(role="model", parts=[part]), partial=True)


class _ScriptedLlm:
    """BaseLlm stand-in. Each call to generate_content_async consumes the
    next run from `runs`; a run is a list of LlmResponse (yield) and/or
    Exception (raise at that point)."""

    def __init__(self, model: str, runs: list[list[Any]], provider_hint: str = "fake") -> None:
        self.model = model
        self.provider_hint = provider_hint
        self.runs = runs
        self.calls = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        run = self.runs[min(self.calls, len(self.runs) - 1)]
        self.calls += 1
        for item in run:
            if isinstance(item, Exception):
                raise item
            yield item


def _rate_limit(msg: str = "rate limited") -> litellm.RateLimitError:
    return litellm.RateLimitError(msg, llm_provider="anthropic", model="claude-sonnet-4-6")


def _overloaded() -> litellm.InternalServerError:
    return litellm.InternalServerError("Overloaded", llm_provider="anthropic", model="claude-sonnet-4-6")


def _bad_request() -> litellm.BadRequestError:
    return litellm.BadRequestError("bad shape", llm_provider="anthropic", model="claude-sonnet-4-6")


def _auth() -> litellm.AuthenticationError:
    return litellm.AuthenticationError("bad key", llm_provider="anthropic", model="claude-sonnet-4-6")


# --- Harness ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch):
    """Reset the module-level provider-health registry and replace real
    sleeping with a recorder (tests must not actually back off)."""
    rl.reset_provider_health()
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(rl, "_async_sleep", _fake_sleep)
    yield sleeps


def _wrap(*models: _ScriptedLlm, **kwargs) -> tuple[ResilientLlm, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    wrapper = ResilientLlm(
        chain=list(models),
        event_sink=lambda name, value: events.append((name, value)),
        **kwargs,
    )
    return wrapper, events


async def _run(wrapper: ResilientLlm) -> list[LlmResponse]:
    return [r async for r in wrapper.generate_content_async(llm_request=None, stream=True)]


# --- 1. Passthrough ---------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_passthrough_is_identical(_clean_slate):
    responses = [_thought("hmm"), _text("Hel"), _text("lo"), _text("Hello", partial=False)]
    primary = _ScriptedLlm("claude-sonnet-4-6", [list(responses)])
    wrapper, events = _wrap(primary)

    out = await _run(wrapper)

    assert out == responses  # same objects, same order — no transformation
    assert all(a is b for a, b in zip(out, responses, strict=True))
    assert events == []
    assert primary.calls == 1


# --- 2. Retry (transient, pre-visible) --------------------------------------


@pytest.mark.asyncio
async def test_transient_failure_retries_same_model_with_backoff(_clean_slate):
    sleeps = _clean_slate
    ok = [_text("answer", partial=False)]
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_rate_limit()], [_overloaded()], ok])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("nope", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["answer"]
    assert primary.calls == 3  # initial + 2 retries
    assert fallback.calls == 0
    retry_events = [e for e in events if e[0] == "MODEL_RETRY"]
    assert len(retry_events) == 2
    assert retry_events[0][1]["model"] == "claude-sonnet-4-6"
    assert retry_events[0][1]["attempt"] == 1
    assert len(sleeps) == 2
    assert all(0 < s <= rl.BACKOFF_CAP_SECONDS for s in sleeps)


@pytest.mark.asyncio
async def test_retry_after_from_provider_takes_precedence(_clean_slate):
    sleeps = _clean_slate
    primary = _ScriptedLlm(
        "claude-sonnet-4-6",
        [[_rate_limit("rate limited. retryDelay: 7s")], [_text("ok", partial=False)]],
    )
    wrapper, _ = _wrap(primary)

    await _run(wrapper)

    assert sleeps == [7.0]


# --- 3. Fallback ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_exhausted_falls_back_with_event(_clean_slate):
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_overloaded()]])  # fails every run
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("saved", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    out = await _run(wrapper)

    assert out[-1].content.parts[0].text == "saved"
    assert primary.calls == 3
    assert fallback.calls == 1
    fb = [e for e in events if e[0] == "MODEL_FALLBACK"]
    assert len(fb) == 1
    assert fb[0][1]["from_model"] == "claude-sonnet-4-6"
    assert fb[0][1]["to_model"] == "gemini-3-1-pro"
    assert fb[0][1]["code"] == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_auth_failure_falls_back_immediately_without_retries(_clean_slate):
    sleeps = _clean_slate
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_auth()]])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("saved", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    out = await _run(wrapper)

    assert out[-1].content.parts[0].text == "saved"
    assert primary.calls == 1  # no same-model retries on auth
    assert sleeps == []
    assert [e[0] for e in events] == ["MODEL_FALLBACK"]


@pytest.mark.asyncio
async def test_non_fallbackable_error_raises_immediately(_clean_slate):
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_bad_request()]])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("never", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    with pytest.raises(ModelTurnError) as exc_info:
        await _run(wrapper)

    assert exc_info.value.error_class.code == "MODEL_REQUEST_INVALID"
    assert primary.calls == 1
    assert fallback.calls == 0
    assert events == []


# --- 4. Visible-output gate --------------------------------------------------


@pytest.mark.asyncio
async def test_no_fallback_after_visible_output(_clean_slate):
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_text("partial ans"), _overloaded()]])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("never", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    with pytest.raises(ModelTurnError):
        await _run(wrapper)

    assert primary.calls == 1
    assert fallback.calls == 0  # visible output already reached the user
    assert not [e for e in events if e[0] == "MODEL_FALLBACK"]


@pytest.mark.asyncio
async def test_thought_only_output_still_falls_back(_clean_slate):
    """Thinking never reached the user as *answer* content — a repeated
    thinking phase on the fallback model beats a dead turn."""
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_thought("deep analysis"), _overloaded()]])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("saved", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    out = await _run(wrapper)

    assert out[-1].content.parts[0].text == "saved"
    assert fallback.calls == 1
    assert [e[0] for e in events].count("MODEL_FALLBACK") == 1


# --- 5. Provider cooldown -----------------------------------------------------


@pytest.mark.asyncio
async def test_provider_cooldown_skips_benched_primary(_clean_slate):
    fallback_runs = [[_text("saved", partial=False)]]
    wrapper_events = []
    # 3 consecutive turns where the primary's provider fails hard.
    for _ in range(3):
        primary = _ScriptedLlm("claude-sonnet-4-6", [[_auth()]], provider_hint="anthropic")
        fallback = _ScriptedLlm("gemini-3-1-pro", list(fallback_runs), provider_hint="gemini")
        wrapper, events = _wrap(primary, fallback)
        await _run(wrapper)
        wrapper_events.append(events)

    # 4th turn: provider is benched — primary must not even be attempted.
    primary4 = _ScriptedLlm("claude-sonnet-4-6", [[_auth()]], provider_hint="anthropic")
    fallback4 = _ScriptedLlm("gemini-3-1-pro", list(fallback_runs), provider_hint="gemini")
    wrapper4, events4 = _wrap(primary4, fallback4)
    out = await _run(wrapper4)

    assert out[-1].content.parts[0].text == "saved"
    assert primary4.calls == 0
    skip = [e for e in events4 if e[0] == "MODEL_FALLBACK"]
    assert len(skip) == 1
    assert skip[0][1].get("reason") == "provider_cooldown"


@pytest.mark.asyncio
async def test_success_resets_provider_health(_clean_slate):
    # Two failures, then a success — the streak must reset, so two MORE
    # failures still don't bench the provider (threshold is 3 consecutive).
    for runs in ([[_auth()]], [[_auth()]], [[_text("ok", partial=False)]], [[_auth()]], [[_auth()]]):
        primary = _ScriptedLlm("claude-sonnet-4-6", runs, provider_hint="anthropic")
        fallback = _ScriptedLlm("gemini-3-1-pro", [[_text("saved", partial=False)]], provider_hint="gemini")
        wrapper, _ = _wrap(primary, fallback)
        await _run(wrapper)

    final_primary = _ScriptedLlm("claude-sonnet-4-6", [[_text("healthy", partial=False)]], provider_hint="anthropic")
    wrapper, _ = _wrap(final_primary)
    out = await _run(wrapper)
    assert final_primary.calls == 1  # NOT benched
    assert out[-1].content.parts[0].text == "healthy"


# --- 6. Chain exhaustion -------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_exhausted_raises_model_turn_error_with_last_class(_clean_slate):
    primary = _ScriptedLlm("claude-sonnet-4-6", [[_overloaded()]])
    fallback = _ScriptedLlm("gemini-3-1-pro", [[_rate_limit()]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)

    with pytest.raises(ModelTurnError) as exc_info:
        await _run(wrapper)

    assert exc_info.value.error_class.code == "MODEL_RATE_LIMITED"  # LAST failure
    assert exc_info.value.model == "gemini-3-1-pro"
    assert [e[0] for e in events].count("MODEL_FALLBACK") == 1


@pytest.mark.asyncio
async def test_single_model_chain_behaves_like_bare_model_on_success(_clean_slate):
    primary = _ScriptedLlm("gemini-3-flash", [[_text("fine", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary)
    out = await _run(wrapper)
    assert out[-1].content.parts[0].text == "fine"
    assert events == []


# --- 7. Fault injection (M4) ---------------------------------------------------


@pytest.mark.asyncio
async def test_fault_injection_forces_fallback(monkeypatch, _clean_slate):
    monkeypatch.setenv("FAULT_INJECT_MODEL", "anthropic:429:2")
    monkeypatch.delenv("K_SERVICE", raising=False)
    rl.reset_fault_injection()

    primary = _ScriptedLlm("claude-opus-4-7", [[_text("real answer", partial=False)]], provider_hint="anthropic")
    fallback = _ScriptedLlm("gemini-2-5-pro", [[_text("backup answer", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback, max_retries_per_model=1)

    out = await _run(wrapper)

    # 2 injected faults > 1 retry -> primary abandoned without ever running.
    assert out[-1].content.parts[0].text == "backup answer"
    assert primary.calls == 0
    assert [e[0] for e in events] == ["MODEL_RETRY", "MODEL_FALLBACK"]


@pytest.mark.asyncio
async def test_fault_injection_refuses_to_arm_on_cloud_run(monkeypatch, _clean_slate):
    monkeypatch.setenv("FAULT_INJECT_MODEL", "anthropic:429:99")
    monkeypatch.setenv("K_SERVICE", "platform-frontend")
    rl.reset_fault_injection()

    primary = _ScriptedLlm("claude-opus-4-7", [[_text("real answer", partial=False)]], provider_hint="anthropic")
    wrapper, events = _wrap(primary)

    out = await _run(wrapper)

    assert out[-1].content.parts[0].text == "real answer"
    assert primary.calls == 1
    assert events == []


@pytest.mark.asyncio
async def test_fallback_rewrites_llm_request_model_to_member(_clean_slate):
    """ROOT-CAUSE guard (2026-07-16). ADK stamps llm_request.model ONCE with the
    chain's primary. Each member must be called with ITS OWN model — otherwise a
    Gemini fallback is handed the primary's id (claude-opus-4-8) and 404s on
    Vertex's anthropic Model-Garden publisher path. Every fallback target failed
    for this reason until the per-member rewrite."""
    seen: list[str | None] = []

    class _Recorder(_ScriptedLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            seen.append(getattr(llm_request, "model", None))
            async for r in super().generate_content_async(llm_request, stream):
                yield r

    primary = _Recorder("claude-opus-4-8", [[_auth()]])  # non-transient, fallbackable → immediate
    fallback = _Recorder("gemini-2.5-flash-lite", [[_text("ok", partial=False)]])
    wrapper, _events = _wrap(primary, fallback)

    class _Req:
        model = "claude-opus-4-8"  # what ADK stamps (the chain primary)

    req = _Req()
    out = [r async for r in wrapper.generate_content_async(llm_request=req, stream=True)]

    assert [r.content.parts[0].text for r in out] == ["ok"]
    # Each member saw ITS OWN model, not the primary's, on its call.
    assert seen == ["claude-opus-4-8", "gemini-2.5-flash-lite"]
    # And the request is left naming the member that actually answered.
    assert req.model == "gemini-2.5-flash-lite"


# --- Cross-provider tool-history sanitizer (v6.13.0) -------------------------
from adk.resilient_llm import sanitize_cross_provider_tool_history  # noqa: E402


class _Req:
    def __init__(self, contents):
        self.contents = contents


def _call(name, id=None):
    return types.Part(function_call=types.FunctionCall(name=name, args={}, id=id))


def _resp(name, id=None):
    return types.Part(function_response=types.FunctionResponse(name=name, response={"ok": True}, id=id))


def _mc(role, parts):
    return types.Content(role=role, parts=parts)


def test_sanitize_backfills_and_matches_gemini_ids():
    # A Gemini-produced tool loop: functionCall + functionResponse, NO ids.
    req = _Req([_mc("model", [_call("search")]), _mc("user", [_resp("search")])])
    assert sanitize_cross_provider_tool_history(req) is True
    call = req.contents[0].parts[0].function_call
    resp = req.contents[1].parts[0].function_response
    assert call.id  # backfilled
    assert resp.id == call.id  # matched — so Anthropic/OpenAI can link them


def test_sanitize_drops_orphan_response():
    # A response with no matching call breaks Anthropic — drop it.
    req = _Req([_mc("user", [_resp("ghost")])])
    assert sanitize_cross_provider_tool_history(req) is True
    assert req.contents[0].parts == []


def test_sanitize_noop_on_native_idd_history():
    # A native Anthropic/OpenAI history (all ids present + matched) is untouched.
    req = _Req([_mc("model", [_call("search", id="toolu_1")]), _mc("user", [_resp("search", id="toolu_1")])])
    assert sanitize_cross_provider_tool_history(req) is False
    assert req.contents[1].parts[0].function_response.id == "toolu_1"


def test_sanitize_noop_when_no_tool_history():
    req = _Req([_mc("user", [types.Part(text="hello")])])
    assert sanitize_cross_provider_tool_history(req) is False


# --- 7. Silence is a failure too (first-token deadline) ---------------------
#
# The classifier only ever saw exceptions. A provider that accepts a streamed
# request and sends NOTHING raises nothing, so retries and fallbacks never
# fired: a deployed fork's prod logged six turns at 48-105 s to first token in
# thirty days, on requests logged as sent and then silent, while the client
# abandoned the stream at 30 s. The deadline turns silence into a
# ``SilentStallError`` BEFORE any output, where the existing rule (retry the
# transient, then fall back) already knows what to do.


class _Stall:
    """Marker in a scripted run: 'this attempt would hang here'."""


class _StallingLlm(_ScriptedLlm):
    """``_ScriptedLlm`` that sleeps forever on a ``_Stall`` marker and records
    whether an abandoned generator was closed."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.closed = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        run = self.runs[min(self.calls, len(self.runs) - 1)]
        self.calls += 1
        try:
            for item in run:
                if isinstance(item, _Stall):
                    await asyncio.sleep(3600)
                elif isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed += 1


@pytest.fixture
def _short_deadline(monkeypatch):
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "0.05")


@pytest.mark.asyncio
async def test_a_stalled_first_attempt_is_retried_and_the_retry_answers(_clean_slate, _short_deadline):
    llm = _StallingLlm("m1", [[_Stall()], [_text("hej"), _text(" der", partial=False)]])
    wrapper, events = _wrap(llm)

    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["hej", " der"]
    assert llm.calls == 2
    assert llm.closed == 2  # the abandoned request was torn down, not left streaming into nowhere
    assert [name for name, _ in events] == [rl.MODEL_RETRY_EVENT]
    assert events[0][1]["code"] == "MODEL_SILENT_STALL"


@pytest.mark.asyncio
async def test_a_persistent_stall_falls_back_down_the_chain(_clean_slate, _short_deadline):
    primary = _StallingLlm("m1", [[_Stall()]])
    fallback = _ScriptedLlm("m2", [[_text("ok", partial=False)]])
    wrapper, events = _wrap(primary, fallback, max_retries_per_model=1)

    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["ok"]
    assert primary.calls == 1  # its deadline is already spent; use the alternative immediately
    assert primary.closed == 1
    assert [name for name, _ in events] == [rl.MODEL_FALLBACK_EVENT]
    assert events[0][1]["code"] == "MODEL_SILENT_STALL"


@pytest.mark.asyncio
async def test_a_stall_with_no_fallback_left_is_a_typed_error(_clean_slate, _short_deadline):
    llm = _StallingLlm("m1", [[_Stall()]])
    wrapper, _ = _wrap(llm, max_retries_per_model=1)

    with pytest.raises(ModelTurnError) as info:
        await _run(wrapper)
    assert info.value.error_class.code == "MODEL_SILENT_STALL"


@pytest.mark.asyncio
async def test_the_deadline_never_applies_once_output_has_started(_clean_slate, _short_deadline):
    """A slow tail is not a stall: once anything has arrived the stream runs
    to completion however long the rest takes."""

    class _SlowTail(_ScriptedLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            yield _text("first")
            await asyncio.sleep(0.2)  # well past the 0.05 s deadline
            yield _text("second", partial=False)

    wrapper, events = _wrap(_SlowTail("m1", [[]]))
    out = await _run(wrapper)
    assert [r.content.parts[0].text for r in out] == ["first", "second"]
    assert events == []


@pytest.mark.asyncio
async def test_a_thought_counts_as_life_for_the_deadline(_clean_slate, _short_deadline):
    """Visible output gates retries; ANY output gates the stall clock. A model
    that thinks for a while before speaking is alive, not stalled."""

    class _ThinksFirst(_ScriptedLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            yield _thought("hmm")
            await asyncio.sleep(0.2)
            yield _text("answer", partial=False)

    wrapper, events = _wrap(_ThinksFirst("m1", [[]]))
    out = await _run(wrapper)
    assert len(out) == 2 and events == []


@pytest.mark.asyncio
async def test_a_non_streamed_call_is_never_bounded(_clean_slate, _short_deadline):
    """Its single chunk IS the whole answer; a long answer is not a stall."""

    class _SlowWhole(_ScriptedLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            await asyncio.sleep(0.2)
            yield _text("the whole answer", partial=False)

    wrapper, events = _wrap(_SlowWhole("m1", [[]]))
    out = [r async for r in wrapper.generate_content_async(llm_request=None, stream=False)]
    assert len(out) == 1 and events == []


def test_deadline_config(monkeypatch):
    monkeypatch.delenv(rl.FIRST_TOKEN_DEADLINE_ENV, raising=False)
    assert rl.first_token_deadline() == 10.0
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "0")
    assert rl.first_token_deadline() is None
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "soon")
    assert rl.first_token_deadline() == 10.0  # garbage must not crash a turn


# A flat 10 s killed a document-heavy specialist skill on a deployed fork's prod
# (2026-10-16/21): its long history put
# gemini's first chunk at ~10-12 s, so every member of the chain "stalled" three
# times on a request that was simply big, and the turn died with RUN_ERROR.


def test_the_deadline_grows_with_the_prompt_and_is_capped(monkeypatch):
    monkeypatch.delenv(rl.FIRST_TOKEN_DEADLINE_ENV, raising=False)
    assert rl.first_token_deadline_for(0) == 10.0  # small prompts keep the measured 10 s
    assert rl.first_token_deadline_for(1_200_000) == pytest.approx(22.0)  # probed 9.6-15.4 s
    assert rl.first_token_deadline_for(100_000_000) == rl.FIRST_TOKEN_DEADLINE_MAX_S
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "0")
    assert rl.first_token_deadline_for(1_200_000) is None  # disabled stays disabled


def test_a_stall_doubles_the_deadline_once(monkeypatch):
    monkeypatch.delenv(rl.FIRST_TOKEN_DEADLINE_ENV, raising=False)
    assert rl.first_token_deadline_for(0, stalls=1) == 20.0
    assert rl.first_token_deadline_for(0, stalls=3) == 20.0  # not compounding: real stalls run 48-105 s


def test_prompt_size_counts_instruction_text_and_tool_traffic():
    class _Cfg:
        system_instruction = "x" * 100

    req = _Req(
        [
            _mc("user", [types.Part(text="y" * 1000)]),
            _mc("model", [types.Part(function_call=types.FunctionCall(name="t", args={"q": "z" * 50}))]),
            _mc("user", [types.Part(function_response=types.FunctionResponse(name="t", response={"r": "w" * 5000}))]),
        ]
    )
    req.config = _Cfg()
    assert rl._prompt_chars(req) > 100 + 1000 + 50 + 5000
    assert rl._prompt_chars(None) == 0  # never raises on a missing request


@pytest.mark.asyncio
async def test_a_request_that_needs_slightly_longer_than_the_deadline_answers_on_retry(_clean_slate, monkeypatch):
    """The prod failure shape: first chunk just past the deadline on every
    attempt. Re-sending under the same deadline can never succeed; the retry
    gets more room and answers."""
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "0.2")

    class _SlowFirstChunk(_ScriptedLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            self.calls += 1
            await asyncio.sleep(0.3)  # past 0.2, inside the doubled 0.4
            yield _text("answer", partial=False)

    llm = _SlowFirstChunk("m1", [[]])
    wrapper, events = _wrap(llm)
    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["answer"]
    assert llm.calls == 2
    assert [name for name, _ in events] == [rl.MODEL_RETRY_EVENT]


# --- Activity progress: which model the turn is waiting on -------------------
# A 2026-10-30 Severa turn spent ~6 minutes across 15 model calls with nothing in
# Activity to say so, and its log named the front door's model instead of the
# transferred specialist's. These pin the progress signals that fix both.


@pytest.fixture
def _bound_tracker(monkeypatch):
    from observability import timing

    monkeypatch.setattr(timing, "_ENABLED", True, raising=True)
    monkeypatch.setattr(timing, "_FULL", True, raising=True)
    tracker = timing.LatencyTracker(skill_id="s", session_id="sess", user_id="u")
    token = timing.set_current_tracker(tracker)
    yield tracker
    timing.reset_current_tracker(token)


def _drained(tracker) -> list[tuple[str, dict]]:
    return [(e.name, e.value) for e in tracker.drain_stage_events()]


@pytest.mark.asyncio
async def test_each_attempt_announces_its_model_and_deadline(_clean_slate, _short_deadline, _bound_tracker):
    primary = _StallingLlm("m1", [[_Stall()]])
    fallback = _ScriptedLlm("m2", [[_text("ok", partial=False)]])
    wrapper = ResilientLlm(chain=[primary, fallback], max_retries_per_model=1)

    await _run(wrapper)

    events = _drained(_bound_tracker)
    calls = [v for name, v in events if name == rl.MODEL_CALL_EVENT]
    assert [c["model"] for c in calls] == ["m1", "m2"]
    assert calls[0]["deadline_s"] is not None
    fallback_event = next(v for name, v in events if name == rl.MODEL_FALLBACK_EVENT)
    # How long the stalled request was waited on — what the Activity row reports.
    assert fallback_event["waited_s"] >= 0.0


@pytest.mark.asyncio
async def test_the_answering_member_becomes_the_turns_model(_clean_slate, _short_deadline, _bound_tracker):
    _bound_tracker.set_model("front-door-lite", "single")
    _drained(_bound_tracker)
    primary = _StallingLlm("m1", [[_Stall()]])
    fallback = _ScriptedLlm("m2", [[_text("ok", partial=False)]])
    wrapper = ResilientLlm(chain=[primary, fallback], max_retries_per_model=1)

    await _run(wrapper)

    resolved = [v["model"] for name, v in _drained(_bound_tracker) if name == "MODEL_RESOLVED"]
    assert resolved == ["m2"]
    assert _bound_tracker._model_used == "m2"  # the timing log names the model that ran


@pytest.mark.asyncio
async def test_housekeeping_chain_stays_out_of_activity(_clean_slate, _bound_tracker):
    _bound_tracker.set_model("front-door-lite", "single")
    _drained(_bound_tracker)
    wrapper = ResilientLlm(chain=[_ScriptedLlm("summariser", [[_text("sum", partial=False)]])])
    wrapper.reports_model = False

    await _run(wrapper)

    assert _drained(_bound_tracker) == []
    assert _bound_tracker._model_used == "front-door-lite"


@pytest.mark.asyncio
async def test_progress_signals_never_reach_the_reliability_sink(_clean_slate, _bound_tracker):
    wrapper, events = _wrap(_ScriptedLlm("m1", [[_text("ok", partial=False)]]))

    await _run(wrapper)

    assert events == []
    assert _drained(_bound_tracker) == []


# --- Per-attempt latency log (event="model_call") ----------------------------
# The turn's ttft line could not compare models: first_model_token is the first
# TEXT on the wire (so a tool-using turn's "ttft" is the whole tool loop), and a
# stall or retry on the way there was invisible in the log. One structured line
# per attempt fixes both; these pin its shape.


def _model_call_lines(caplog) -> list[dict]:
    return [
        r.json_fields
        for r in caplog.records
        if isinstance(getattr(r, "json_fields", None), dict) and r.json_fields.get("event") == rl.MODEL_CALL_LOG_EVENT
    ]


def _with_usage(response: LlmResponse, *, prompt: int, out: int, thoughts: int) -> LlmResponse:
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts
    )
    return response


@pytest.mark.asyncio
async def test_each_attempt_writes_one_model_call_line(_clean_slate, _short_deadline, _bound_tracker, caplog):
    caplog.set_level("INFO", logger=rl.logger.name)
    primary = _StallingLlm("m1", [[_Stall()]])
    fallback = _ScriptedLlm(
        "m2", [[_thought("hmm"), _with_usage(_text("ok", partial=False), prompt=900, out=3, thoughts=40)]]
    )
    wrapper = ResilientLlm(chain=[primary, fallback], max_retries_per_model=1)

    await _run(wrapper)

    lines = _model_call_lines(caplog)
    assert [(x["model"], x["outcome"], x.get("code"), x.get("action")) for x in lines] == [
        ("m1", "error", "MODEL_SILENT_STALL", "fallback"),
        ("m2", "ok", None, None),
    ]
    stalled, answered = lines
    assert stalled["first_chunk_ms"] is None and stalled["duration_ms"] >= 0
    assert answered["chain_index"] == 1 and answered["role"] == "turn"
    # The thought arrives first: first_chunk precedes (or equals) first_visible.
    assert answered["first_chunk_ms"] <= answered["first_visible_ms"] <= answered["duration_ms"]
    assert (answered["prompt_tokens"], answered["output_tokens"], answered["thinking_tokens"]) == (900, 3, 40)
    assert answered["skill_id"] == "s" and answered["session_id"] == "sess"


@pytest.mark.asyncio
async def test_the_turn_line_counts_calls_and_failures(_clean_slate, _short_deadline, _bound_tracker):
    primary = _StallingLlm("m1", [[_Stall()]])
    fallback = _ScriptedLlm("m2", [[_text("ok", partial=False)]])
    wrapper = ResilientLlm(chain=[primary, fallback], max_retries_per_model=1)

    await _run(wrapper)

    payload = _bound_tracker.report_payload()
    assert payload["model_calls"] == 2
    assert payload["model_call_failures"] == ["m1:MODEL_SILENT_STALL:fallback"]
    # The model's own first output, not the first text on the wire.
    assert payload["first_model_output_ms"] >= 0


@pytest.mark.asyncio
async def test_a_retry_is_logged_not_just_signalled(_clean_slate, caplog):
    caplog.set_level("INFO", logger=rl.logger.name)
    llm = _ScriptedLlm("m1", [[_rate_limit()], [_text("ok", partial=False)]])
    wrapper, _ = _wrap(llm)

    await _run(wrapper)

    lines = _model_call_lines(caplog)
    assert [(x["attempt"], x["outcome"], x.get("action")) for x in lines] == [(0, "error", "retry"), (1, "ok", None)]


@pytest.mark.asyncio
async def test_housekeeping_calls_are_logged_but_not_counted(_clean_slate, _bound_tracker, caplog):
    caplog.set_level("INFO", logger=rl.logger.name)
    wrapper = ResilientLlm(chain=[_ScriptedLlm("summariser", [[_text("sum", partial=False)]])])
    wrapper.reports_model = False

    await _run(wrapper)

    assert [x["role"] for x in _model_call_lines(caplog)] == ["housekeeping"]
    assert _bound_tracker.report_payload()["model_calls"] == 0


@pytest.mark.asyncio
async def test_a_consumer_that_stops_after_output_is_closed_not_failed(_clean_slate, _bound_tracker, caplog):
    """ADK stops reading once a transfer_to_agent call is made; on live every
    delegated turn logged its router as a failure until this (2026-11-05)."""
    caplog.set_level("INFO", logger=rl.logger.name)
    wrapper = ResilientLlm(chain=[_ScriptedLlm("m1", [[_text("a"), _text("b"), _text("c", partial=False)]])])

    gen = wrapper.generate_content_async(llm_request=None, stream=True)
    await gen.__anext__()
    await gen.aclose()

    lines = _model_call_lines(caplog)
    assert [(x["outcome"], x["first_visible_ms"] is not None) for x in lines] == [("closed", True)]
    assert _bound_tracker.report_payload()["model_call_failures"] == []


@pytest.mark.asyncio
async def test_a_consumer_that_walks_away_before_output_is_cancelled(_clean_slate, _bound_tracker, caplog):
    caplog.set_level("INFO", logger=rl.logger.name)
    wrapper = ResilientLlm(chain=[_ScriptedLlm("m1", [[_thought("hmm"), _text("late", partial=False)]])])

    gen = wrapper.generate_content_async(llm_request=None, stream=True)
    await gen.__anext__()  # only the thought
    await gen.aclose()

    lines = _model_call_lines(caplog)
    assert [(x["outcome"], x["first_visible_ms"]) for x in lines] == [("cancelled", None)]
    assert _bound_tracker.report_payload()["model_call_failures"] == ["m1:cancelled"]


@pytest.mark.asyncio
async def test_a_benched_member_is_logged_as_skipped(_clean_slate, caplog):
    caplog.set_level("INFO", logger=rl.logger.name)
    for _ in range(rl.COOLDOWN_THRESHOLD):
        rl._record_failure("fake")
    primary = _ScriptedLlm("m1", [[_text("never")]])
    fallback = _ScriptedLlm("m2", [[_text("ok", partial=False)]], provider_hint="other")
    wrapper, _ = _wrap(primary, fallback)

    await _run(wrapper)

    lines = _model_call_lines(caplog)
    assert [(x["model"], x["outcome"], x.get("action")) for x in lines] == [
        ("m1", "skipped", "fallback"),
        ("m2", "ok", None),
    ]


# --- 8. Cross-provider turns (Sunholo, 2026-11-09) ----------------------------
#
# claude-haiku-5-5 took longer than the Gemini-sized 11.7 s deadline to start on
# a 172K-character project chat, the turn fell straight back to Gemini 3.5 Flash
# Lite, and Gemini refused the history: "Function call is missing a
# thought_signature" (the calls were Claude's). The turn died.


def test_claude_gets_more_time_than_gemini_for_the_same_prompt(monkeypatch):
    monkeypatch.delenv(rl.FIRST_TOKEN_DEADLINE_ENV, raising=False)
    gemini = rl.first_token_deadline_for(172_066, provider="gemini")
    claude = rl.first_token_deadline_for(172_066, provider="anthropic")
    assert gemini == pytest.approx(11.72, abs=0.01)  # what fired on Sunholo
    assert claude == pytest.approx(25.32, abs=0.01)  # its observed 8 s first chunk fits with room
    assert rl.first_token_deadline_for(172_066, stalls=1, provider="anthropic") == pytest.approx(50.64, abs=0.01)
    assert rl.first_token_deadline_for(5_000_000, provider="anthropic") == rl.FIRST_TOKEN_DEADLINE_MAX_S


@pytest.mark.asyncio
async def test_a_stall_is_retried_once_before_moving_to_another_provider(_clean_slate, _short_deadline, monkeypatch):
    monkeypatch.setitem(rl.SLOW_PREFILL_PROVIDERS, "anthropic", (0.0, 0.0))  # keep the 0.05 s test deadline
    primary = _StallingLlm("claude", [[_Stall()], [_text("ok", partial=False)]], provider_hint="anthropic")
    other = _ScriptedLlm("gemini", [[_text("fallback", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, other)

    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["ok"]
    assert primary.calls == 2 and other.calls == 0
    assert [name for name, _ in events] == [rl.MODEL_RETRY_EVENT]


@pytest.mark.asyncio
async def test_a_second_stall_does_move_to_another_provider(_clean_slate, _short_deadline, monkeypatch):
    monkeypatch.setitem(rl.SLOW_PREFILL_PROVIDERS, "anthropic", (0.0, 0.0))  # keep the 0.05 s test deadline
    primary = _StallingLlm("claude", [[_Stall()]], provider_hint="anthropic")
    other = _ScriptedLlm("gemini", [[_text("fallback", partial=False)]], provider_hint="gemini")
    wrapper, events = _wrap(primary, other)

    out = await _run(wrapper)

    assert [r.content.parts[0].text for r in out] == ["fallback"]
    assert primary.calls == 2
    assert [name for name, _ in events] == [rl.MODEL_RETRY_EVENT, rl.MODEL_FALLBACK_EVENT]


class _Recording(_ScriptedLlm):
    """Answers, and keeps the thought signatures each function call had when sent."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen: list[bytes | None] = []

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        self.seen = [p.thought_signature for c in llm_request.contents for p in c.parts if p.function_call is not None]
        async for item in super().generate_content_async(llm_request, stream):
            yield item


def _claude_tool_history() -> _Req:
    return _Req(
        [
            _mc("user", [types.Part(text="find it")]),
            _mc("model", [_call("search_project_documents", id="toolu_1")]),
            _mc("user", [_resp("search_project_documents", id="toolu_1")]),
        ]
    )


@pytest.mark.asyncio
async def test_a_gemini_fallback_can_continue_a_claude_tool_loop(_clean_slate):
    claude = _ScriptedLlm("claude", [[RuntimeError("boom")]], provider_hint="anthropic")
    gemini = _Recording("gemini", [[_text("carried on", partial=False)]], provider_hint="gemini")
    wrapper, _ = _wrap(claude, gemini, max_retries_per_model=0)
    req = _claude_tool_history()

    out = [r async for r in wrapper.generate_content_async(llm_request=req, stream=False)]

    assert [r.content.parts[0].text for r in out] == ["carried on"]
    assert gemini.seen == [rl.FOREIGN_CALL_THOUGHT_SIGNATURE]


@pytest.mark.asyncio
async def test_signatures_are_never_added_for_other_providers_or_over_real_ones(_clean_slate):
    claude = _Recording("claude", [[_text("fine", partial=False)]], provider_hint="anthropic")
    wrapper, _ = _wrap(claude)
    req = _claude_tool_history()
    [r async for r in wrapper.generate_content_async(llm_request=req, stream=False)]
    assert claude.seen == [None]

    real = _claude_tool_history()
    real.contents[1].parts[0].thought_signature = b"gemini's own"
    assert rl.backfill_foreign_thought_signatures(real) is False
    assert real.contents[1].parts[0].thought_signature == b"gemini's own"
