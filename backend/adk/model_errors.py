"""MODEL-RELIABILITY M2 — provider error classification.

Single source of truth for "what do we do when a model call fails":

* ``transient``    — worth retrying the SAME model with backoff
* ``fallbackable`` — worth trying the NEXT model in the chain
* ``code``         — the typed RUN_ERROR code the frontend renders
* ``retry_after``  — provider-suggested wait (capped; see below)

Consumed by ``ResilientLlm`` (M3) for retry/fallback decisions and by
``skill_processor`` to translate a dead turn into a typed RUN_ERROR
instead of a silent stream death (the pre-M2 behavior for every
non-Gemini provider).

Empirical grounding (litellm 1.82.6, recorded 2026-07-10 — fixtures in
``tests/unit/test_model_errors.py``): Anthropic's 529 ``overloaded_error``
reaches us as ``litellm.InternalServerError`` with ``status_code=500`` —
the 529 identity is gone by the time we see it, so InternalServerError
is treated uniformly as transient+fallbackable. Don't "improve" this by
matching class names from a mapping table you haven't re-recorded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Honor provider retry-after up to this cap; beyond it, moving down the
# fallback chain beats waiting (axiom #5: <30s total failover).
RETRY_AFTER_CAP_SECONDS = 10.0

CODE_RATE_LIMITED = "MODEL_RATE_LIMITED"
CODE_UNAVAILABLE = "MODEL_UNAVAILABLE"
CODE_AUTH_FAILED = "MODEL_AUTH_FAILED"
CODE_REQUEST_INVALID = "MODEL_REQUEST_INVALID"


@dataclass(frozen=True)
class ErrorClass:
    transient: bool
    fallbackable: bool
    code: str
    retry_after: float | None = None
    provider: str = "unknown"
    status: int | None = None
    message: str = ""


class ModelTurnError(Exception):
    """A model turn failed after all retries and fallbacks were exhausted.

    Carries the classification of the LAST failure so skill_processor can
    emit a typed RUN_ERROR (code, retryability, retry_after) without
    re-deriving anything. Raised with ``from original`` so logs keep the
    full provider traceback.
    """

    def __init__(self, error_class: ErrorClass, model: str) -> None:
        self.error_class = error_class
        self.model = model
        super().__init__(f"model turn failed on {model}: {error_class.code} ({error_class.message[:200]})")


_RETRY_AFTER_RE = re.compile(r"retry(?:Delay|[-_ ]after)?[\"'\s:]*(?:in\s*)?\"?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)

# Provider capacity / billing caps that arrive as a 4xx instead of a 429.
# Anthropic delivers an ORG usage cap as a 400 BadRequest ("You have reached your
# specified API usage limits. You will regain access on ...") — semantically the
# PROVIDER is unavailable to us, NOT the request malformed. Detected by message so
# it routes to FALLBACK (next model in the chain) instead of a dead RUN_ERROR.
# (Live 2026-07-16: claude-opus usage cap killed every Claude-tier turn even
# though the chain has a gemini fallback rung.)
_CAPACITY_RE = re.compile(
    r"usage limit|regain access|over[- ]?quota|quota (?:exceeded|reached)|"
    r"out of credits?|credit balance|insufficient (?:credit|quota|balance|funds)|"
    r"payment required|spend(?:ing)? (?:limit|cap)",
    re.IGNORECASE,
)


def _extract_retry_after(exc: Exception) -> float | None:
    # litellm sometimes exposes a numeric attribute; the message regex covers
    # Gemini's retryDelay JSON and Anthropic's prose forms.
    attr = getattr(exc, "retry_after", None)
    if isinstance(attr, (int, float)) and attr > 0:
        return min(float(attr), RETRY_AFTER_CAP_SECONDS)
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        return min(float(match.group(1)), RETRY_AFTER_CAP_SECONDS)
    return None


def _classify_status(status: int | None, provider: str, message: str, exc: Exception) -> ErrorClass:
    if status == 429:
        return ErrorClass(
            transient=True,
            fallbackable=True,
            code=CODE_RATE_LIMITED,
            retry_after=_extract_retry_after(exc),
            provider=provider,
            status=status,
            message=message,
        )
    if status in (401, 403):
        # Auth is not transient (retrying the same misconfigured key is
        # pointless) but IS worth one fallback try — a bad key on one
        # provider says nothing about the next.
        return ErrorClass(
            transient=False, fallbackable=True, code=CODE_AUTH_FAILED, provider=provider, status=status, message=message
        )
    if status is not None and 400 <= status < 500:
        # A capacity/billing cap dressed as a 4xx means the PROVIDER is
        # unavailable, not the request malformed — fall back to the next model.
        # Not transient: the cap won't clear on a short backoff.
        if _CAPACITY_RE.search(message or ""):
            return ErrorClass(
                transient=False,
                fallbackable=True,
                code=CODE_UNAVAILABLE,
                provider=provider,
                status=status,
                message=message,
            )
        return ErrorClass(
            transient=False,
            fallbackable=False,
            code=CODE_REQUEST_INVALID,
            provider=provider,
            status=status,
            message=message,
        )
    # 5xx / timeouts / connection errors — includes Anthropic 529, which
    # litellm delivers as InternalServerError(status=500).
    return ErrorClass(
        transient=True, fallbackable=True, code=CODE_UNAVAILABLE, provider=provider, status=status, message=message
    )


def classify(exc: Exception) -> ErrorClass:
    """Classify a provider exception; never raises.

    Walks the ``__cause__`` chain first (ADK wraps Gemini 429s in a private
    ``_ResourceExhaustedError(ce) from ce``) so wrappers don't hide the
    classifiable root.
    """
    # Unwrap cause chains (bounded — cycles shouldn't happen, belt anyway).
    seen: list[Exception] = []
    current: Exception | None = exc
    while current is not None and len(seen) < 8:
        seen.append(current)
        current = current.__cause__ if isinstance(current.__cause__, Exception) else None

    for candidate in seen:
        cls = _classify_known(candidate)
        if cls is not None:
            return cls

    # Unknown exception type: one shot at the next provider, no blind
    # same-model retries (a retry storm on a deterministic crash burns the
    # failover budget without changing the outcome).
    logger.warning("model_errors: unclassified exception type %s: %s", type(exc).__name__, str(exc)[:200])
    return ErrorClass(
        transient=False,
        fallbackable=True,
        code=CODE_UNAVAILABLE,
        provider="unknown",
        status=None,
        message=str(exc)[:500],
    )


def _classify_known(exc: Exception) -> ErrorClass | None:
    message = str(exc)[:500]

    # --- google-genai (native Gemini path) ---------------------------------
    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError):
            return _classify_status(getattr(exc, "code", None), "gemini", message, exc)
    except ImportError:  # pragma: no cover - genai is a hard dep in practice
        pass

    # --- litellm (Claude / OpenAI / OpenRouter paths) -----------------------
    try:
        import litellm

        provider = getattr(exc, "llm_provider", None) or "litellm"
        if isinstance(exc, litellm.exceptions.RateLimitError):
            return _classify_status(429, provider, message, exc)
        if isinstance(exc, litellm.exceptions.AuthenticationError | litellm.exceptions.PermissionDeniedError):
            return _classify_status(getattr(exc, "status_code", 401), provider, message, exc)
        # Order matters: ContextWindowExceededError subclasses BadRequestError;
        # both are non-retryable REQUEST_INVALID, so the parent check covers it.
        if isinstance(exc, litellm.exceptions.BadRequestError):
            return _classify_status(getattr(exc, "status_code", 400), provider, message, exc)
        if isinstance(exc, litellm.exceptions.NotFoundError):
            return _classify_status(404, provider, message, exc)
        if isinstance(
            exc,
            litellm.exceptions.InternalServerError
            | litellm.exceptions.ServiceUnavailableError
            | litellm.exceptions.APIConnectionError
            | litellm.exceptions.Timeout,
        ):
            status = getattr(exc, "status_code", None)
            return ErrorClass(
                transient=True,
                fallbackable=True,
                code=CODE_UNAVAILABLE,
                provider=provider,
                status=status,
                message=message,
            )
        if isinstance(exc, litellm.exceptions.APIError):
            return _classify_status(getattr(exc, "status_code", None), provider, message, exc)
    except ImportError:  # pragma: no cover - litellm is a hard dep in practice
        pass

    return None
