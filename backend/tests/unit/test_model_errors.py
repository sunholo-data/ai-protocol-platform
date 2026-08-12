"""MODEL-RELIABILITY M2 — provider error classification (`adk/model_errors.py`).

The classifier is the single source of truth for "what do we do when a
model call fails": retry the same model (transient), move down the
fallback chain (fallbackable), or fail the turn with a typed RUN_ERROR
code. Getting a class wrong is expensive in both directions — retrying
a 400 burns the failover budget on a request that can never succeed;
failing fast on a 429 throws away a turn a 2s backoff would have saved.

Fixture provenance (empirical, litellm 1.82.6 + google-genai, recorded
2026-07-10 — see sprint notes): **Anthropic's 529 `overloaded_error`
maps through LiteLLM to `InternalServerError` with `status_code=500`**,
losing its identity entirely. The classifier therefore treats
InternalServerError uniformly as transient+fallbackable rather than
pretending it can distinguish 529 from 500. ContextWindowExceededError
IS a BadRequestError subclass — order of isinstance checks matters.
"""

from __future__ import annotations

import litellm
import pytest
from google.genai import errors as genai_errors

from adk.model_errors import ErrorClass, classify

# --- Fixture builders -------------------------------------------------------

_LL = {"llm_provider": "anthropic", "model": "claude-sonnet-4-6"}


def _client_error(code: int, status: str, message: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"error": {"message": message, "status": status}})


def _server_error(code: int, status: str, message: str) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"error": {"message": message, "status": status}})


# --- Table-driven classification -------------------------------------------

CASES: list[tuple[str, Exception, dict]] = [
    # LiteLLM (Claude / OpenAI paths)
    (
        "litellm 429 rate limit",
        litellm.RateLimitError("rate limited", **_LL),
        {"transient": True, "fallbackable": True, "code": "MODEL_RATE_LIMITED"},
    ),
    (
        "litellm InternalServerError — Anthropic 529 arrives as this (recorded)",
        litellm.InternalServerError("Overloaded (529 overloaded_error)", **_LL),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "litellm 503 service unavailable",
        litellm.ServiceUnavailableError("upstream unavailable", **_LL),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "litellm connection error",
        litellm.APIConnectionError("connection reset", **_LL),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "litellm timeout",
        litellm.Timeout("request timed out", model="claude-sonnet-4-6", llm_provider="anthropic"),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "litellm auth — bad key on ONE provider doesn't impugn the next",
        litellm.AuthenticationError("invalid x-api-key", **_LL),
        {"transient": False, "fallbackable": True, "code": "MODEL_AUTH_FAILED"},
    ),
    (
        "litellm bad request — can never succeed, fail fast",
        litellm.BadRequestError("invalid request shape", **_LL),
        {"transient": False, "fallbackable": False, "code": "MODEL_REQUEST_INVALID"},
    ),
    (
        # Anthropic delivers an ORG usage/billing cap as a 400 BadRequest, not a
        # 429 — but the PROVIDER is unavailable, not the request malformed. Must
        # fall back to the next model (live 2026-07-16). NOT transient.
        "litellm usage cap dressed as 400 — provider unavailable, fall back",
        litellm.BadRequestError(
            "AnthropicException - You have reached your specified API usage limits. "
            "You will regain access on 2026-08-01.",
            **_LL,
        ),
        {"transient": False, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "litellm context window — subclass of BadRequestError, same verdict",
        litellm.ContextWindowExceededError("prompt too long", model="claude-sonnet-4-6", llm_provider="anthropic"),
        {"transient": False, "fallbackable": False, "code": "MODEL_REQUEST_INVALID"},
    ),
    # google-genai (Gemini path)
    (
        "gemini 429 RESOURCE_EXHAUSTED",
        _client_error(429, "RESOURCE_EXHAUSTED", "Resource exhausted"),
        {"transient": True, "fallbackable": True, "code": "MODEL_RATE_LIMITED"},
    ),
    (
        "gemini 401 — quota-project drift class of failure",
        _client_error(401, "UNAUTHENTICATED", "CREDENTIALS_MISSING"),
        {"transient": False, "fallbackable": True, "code": "MODEL_AUTH_FAILED"},
    ),
    (
        "gemini 400 invalid",
        _client_error(400, "INVALID_ARGUMENT", "too many states for serving"),
        {"transient": False, "fallbackable": False, "code": "MODEL_REQUEST_INVALID"},
    ),
    (
        "gemini 500",
        _server_error(500, "INTERNAL", "internal error"),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
    (
        "gemini 503 UNAVAILABLE",
        _server_error(503, "UNAVAILABLE", "service unavailable"),
        {"transient": True, "fallbackable": True, "code": "MODEL_UNAVAILABLE"},
    ),
]


@pytest.mark.parametrize(("label", "exc", "expected"), CASES, ids=[c[0] for c in CASES])
def test_classification_table(label: str, exc: Exception, expected: dict) -> None:
    cls = classify(exc)
    assert isinstance(cls, ErrorClass)
    for key, value in expected.items():
        assert getattr(cls, key) == value, f"{label}: {key} should be {value}, got {getattr(cls, key)}"


def test_adk_resource_exhausted_wrapper_unwraps_to_rate_limited() -> None:
    """ADK wraps Gemini 429s in `_ResourceExhaustedError(ce) from ce` — the
    classifier must unwrap the __cause__ chain rather than seeing an unknown
    exception type."""
    inner = _client_error(429, "RESOURCE_EXHAUSTED", "Resource exhausted")

    class LookalikeWrapper(Exception):
        """Same shape ADK's private _ResourceExhaustedError has."""

    wrapper = LookalikeWrapper("enhanced 429 messaging")
    wrapper.__cause__ = inner

    cls = classify(wrapper)
    assert cls.code == "MODEL_RATE_LIMITED"
    assert cls.transient and cls.fallbackable


def test_unknown_exception_is_fallbackable_but_not_retried() -> None:
    """An exception we can't identify gets ONE shot at the next provider
    (fallbackable) but no blind same-model retries (a retry storm on a
    deterministic crash burns the <30s failover budget)."""
    cls = classify(RuntimeError("totally novel failure"))
    assert cls.code == "MODEL_UNAVAILABLE"
    assert cls.transient is False
    assert cls.fallbackable is True


def test_retry_after_extracted_and_capped() -> None:
    """Anthropic 429s carry retry-after; honor it but cap at 10s — beyond
    that, falling back beats waiting (axiom #5's <30s failover budget)."""
    short = litellm.RateLimitError("rate limited. retryDelay: 7s", **_LL)
    cls = classify(short)
    assert cls.retry_after == pytest.approx(7.0)

    long = litellm.RateLimitError("rate limited. retryDelay: 55s", **_LL)
    cls_long = classify(long)
    assert cls_long.retry_after == pytest.approx(10.0)


def test_provider_and_status_recorded_for_observability() -> None:
    """MODEL_RETRY/MODEL_FALLBACK events and OTel counters need provider +
    status without leaking prompt content."""
    cls = classify(litellm.RateLimitError("rate limited", **_LL))
    assert cls.provider == "anthropic"
    assert cls.status == 429

    gcls = classify(_server_error(503, "UNAVAILABLE", "unavailable"))
    assert gcls.provider == "gemini"
    assert gcls.status == 503
