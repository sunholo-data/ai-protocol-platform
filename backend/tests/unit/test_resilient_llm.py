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
