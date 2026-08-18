"""Resilient wrapper for tool-internal Vertex structured-output calls (v6.14.0).

Tools like ``map_ppa_obligations``, ``extract_ppa_clauses``,
``compare_ppa_contracts`` and ``structured_extraction`` make their OWN raw
``genai.Client(vertexai=True).generate_content`` calls — a path SEPARATE from the
main agent's LLM, which is protected by ``ResilientLlm``. So a transient Vertex
429 RESOURCE_EXHAUSTED (quota) inside one of these tools became a hard, silent,
minutes-long dead-end (observed 2026-07-17: an obligation analysis hung ~3.5min
then failed on a 429 with no retry, no fallback, no user feedback).

This module gives those calls the same resilience the agent already has, adapted
to the raw genai path:

* **Retry** the same model with capped full-jitter backoff on a *transient* error
  (429 / 503 / timeout), classified by ``adk.model_errors.classify`` (which
  already understands ``google.genai`` ``APIError``).
* **Fall back** across a **Gemini-only** chain — Vertex ``response_schema``
  structured output is Gemini-only, so we fail over to another region (dodging a
  region's per-model quota) or another Gemini tier (separate quota), NOT to
  Claude/OpenAI. The chain comes from the model registry's own ``fallbacks``
  (``config/models.yaml``) where the ref is a tier/registry id; a raw-model caller
  gets a synthesized cross-region rung.
* **Signal** every retry / fallback as ``MODEL_RETRY`` / ``MODEL_FALLBACK`` on the
  same per-request event queue the agent uses (``emit_reliability_event``), so the
  degradation is visible to the user *during* the backoff — not a silent hang.
* **Announce** the slow phase up front via a ``STAGE_PROGRESS`` label
  ("Mapping obligations…"), so even a first-try slow call shows a working state.

Mirrors ``adk.resilient_llm`` (same backoff constants, same event names, same
classify) but for the raw ``generate_content`` seam rather than the ``BaseLlm``
one. Retries live in ONE layer here too: do not add genai client-level retries on
top, or attempts multiply.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

from google import genai

from adk.model_errors import ModelTurnError, classify
from config.models import ModelEntry, entry_for, gemini_api_name_for

logger = logging.getLogger(__name__)

# Mirror resilient_llm's backoff so the two layers behave identically.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0
RETRY_AFTER_CAP_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2

# Alternate Vertex region for the synthesized failover rung (raw-model callers
# with no registry fallback chain). europe-west4 is the same cross-region rung the
# registry uses for the `pro` tier, so it is known-good and EU-resident.
_CROSS_REGION = os.environ.get("GENAI_FALLBACK_REGION", "europe-west4")

MODEL_RETRY_EVENT = "MODEL_RETRY"
MODEL_FALLBACK_EVENT = "MODEL_FALLBACK"

# Patchable seam for tests (real backoff sleeps would slow the suite).
_async_sleep = asyncio.sleep


@dataclass(frozen=True)
class _Rung:
    """One member of the Gemini failover chain: an api name + optional region."""

    api_name: str
    location: str | None

    @property
    def label(self) -> str:
        return f"{self.api_name}@{self.location}" if self.location else self.api_name


def _location_for(entry: ModelEntry | None, explicit: str | None) -> str | None:
    """Vertex location a rung's client must pin (None = the env default region).

    An explicit cross-region override (a tier-1a fallback ``ChainLink.location``)
    wins. Otherwise an entry with its own pinned ``location`` (e.g. the
    ``-eu`` entries added 2026-08-13 for Gemini 3.x models whose only EU
    option is the "eu" jurisdictional multi-region endpoint, not a
    europe-west* region) uses that. Otherwise a ``residency: global`` model
    MUST be called at ``location="global"`` — it 404s on the default
    region-pinned client (``GOOGLE_CLOUD_LOCATION=europe-west1``): the exact
    failure the PPA pipeline hit on every *unrestricted* env once the
    ``lite``/``pro`` tiers resolved to the global-endpoint 3.x line (e.g.
    ``gemini-3.5-flash-lite`` 404 in europe-west1). Mirrors
    ``adk.agent.resolve_model``'s ``RegionalGemini`` branches — the agent path
    already pins both cases; this raw-genai seam missed them.
    """
    if explicit:
        return explicit
    if entry is not None and entry.location:
        return entry.location
    if entry is not None and entry.residency == "global":
        return "global"
    return None


def gemini_chain_for(model_ref: str) -> list[_Rung]:
    """Build the Gemini-only failover chain for a tier / registry id / raw api name.

    Rung 0 is the resolved primary, pinned to its residency-correct location
    (``global`` for a global-endpoint model, else the env default region). Then
    the registry's own Gemini ``fallbacks`` for that entry (region and/or model
    failover). If the ref has no registry entry (a raw ``gemini-*`` api name), a
    single cross-region rung is synthesized so even raw-model callers get one
    failover try.

    Raises:
        ValueError: if the ref resolves to a non-Gemini model — structured output
            (``response_schema``) is Gemini-only, so a Claude/OpenAI tier here is a
            configuration error, surfaced loudly rather than as a broken request.
    """
    primary = gemini_api_name_for(model_ref)  # raises for non-Gemini
    entry = entry_for(model_ref)
    rungs: list[_Rung] = [_Rung(primary, _location_for(entry, None))]

    if entry is not None:
        for link in entry.fallbacks:
            fe = entry_for(link.id)
            api = fe.api_name if fe is not None else link.id
            if api.startswith("gemini-"):  # Gemini-only failover
                rungs.append(_Rung(api, _location_for(fe, link.location)))

    # Raw-model caller (no registry chain): synthesize one cross-region rung.
    # A global primary serves ONLY at location="global" (a cross-region rung would
    # 404 there too), so never append one after a global rung 0.
    if len(rungs) == 1 and rungs[0].location != "global":
        rungs.append(_Rung(primary, _CROSS_REGION))

    # De-dupe while preserving order (a tier could list its primary region again).
    seen: set[tuple[str, str | None]] = set()
    out: list[_Rung] = []
    for r in rungs:
        key = (r.api_name, r.location)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _backoff_delay(attempt: int) -> float:
    """Full jitter, capped; floor keeps 'slept a positive amount' observable."""
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
    return max(0.05, random.uniform(0, ceiling))


def _emit(name: str, value: dict) -> None:
    """Enqueue a CUSTOM event on the per-request tracker (fail-open).

    Rides the SAME un-gated queue ``ResilientLlm`` uses, so a retry/fallback notice
    reaches the user mid-backoff. No-op (and never raises) when no tracker is bound
    — e.g. a CLI/eval run outside an SSE request.
    """
    try:
        from observability.timing import get_current_tracker

        get_current_tracker().emit_reliability_event(name, value)
    except Exception as exc:  # instrumentation must never break the tool
        logger.debug("resilient_genai: event emit suppressed: %s", exc)


def emit_stage_progress(label: str) -> None:
    """Show a working-state label ("Mapping obligations…") in the typing indicator.

    Uses the un-gated reliability queue rather than ``LatencyTracker.mark`` because
    ``mark``'s STAGE_PROGRESS is gated on TTFT ``full`` mode (off in production), so
    a long tool phase would otherwise be a silent wait. Fail-open.
    """
    try:
        from observability.timing import STAGE_PROGRESS_EVENT_NAME, get_current_tracker

        get_current_tracker().emit_reliability_event(STAGE_PROGRESS_EVENT_NAME, {"stage": "tool", "label": label})
    except Exception as exc:
        logger.debug("resilient_genai: progress emit suppressed: %s", exc)


def _client_for(rung: _Rung) -> Any:
    """A Vertex genai client, region-pinned when the rung overrides the location."""
    if rung.location:
        return genai.Client(vertexai=True, location=rung.location)
    return genai.Client(vertexai=True)


async def generate_content_resilient(
    *,
    prompt: str,
    model_ref: str,
    config: dict,
    progress_label: str | None = None,
    label: str = "genai",
    max_retries_per_model: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """Run a Vertex structured-output call with retry + Gemini failover.

    Args:
        prompt: The full prompt string (``contents``).
        model_ref: A tier name (``"pro"``), registry id, or raw ``gemini-*`` api
            name. Resolved to the primary + a Gemini-only failover chain.
        config: The ``generate_content`` config dict (e.g. ``response_mime_type``,
            ``response_schema``, ``max_output_tokens``). The per-rung ``model`` is
            set by this helper — do not put it in ``config``.
        progress_label: Optional working-state label emitted before the first
            attempt (STAGE_PROGRESS) so a slow call isn't a silent wait.
        label: Short tag for logs/events (e.g. ``"obligation-mapping"``).
        max_retries_per_model: Same-model transient retries before falling back.

    Returns:
        The raw ``GenerateContentResponse`` — callers keep their own
        ``.text``/finish-reason handling unchanged.

    Raises:
        ModelTurnError: after the whole chain is exhausted, carrying the last
            failure's classification (code / retryability) for the caller to wrap.
    """
    if progress_label:
        emit_stage_progress(progress_label)

    chain = gemini_chain_for(model_ref)
    last_exc: Exception | None = None
    last_class = None
    last_model = "unknown"

    for idx, rung in enumerate(chain):
        has_next = idx + 1 < len(chain)
        client = _client_for(rung)
        attempt = 0
        while True:
            try:
                return await client.aio.models.generate_content(
                    model=rung.api_name,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:
                error_class = classify(exc)
                last_exc, last_class, last_model = exc, error_class, rung.label

                if error_class.transient and attempt < max_retries_per_model:
                    attempt += 1
                    delay = (
                        min(error_class.retry_after, RETRY_AFTER_CAP_SECONDS)
                        if error_class.retry_after
                        else _backoff_delay(attempt)
                    )
                    logger.warning(
                        "resilient_genai[%s]: %s transient (%s) — retry %d in %.2fs",
                        label,
                        rung.label,
                        error_class.code,
                        attempt,
                        delay,
                    )
                    _emit(
                        MODEL_RETRY_EVENT,
                        {
                            "model": rung.label,
                            "attempt": attempt,
                            "delay_s": round(delay, 2),
                            "code": error_class.code,
                            "provider": "gemini",
                        },
                    )
                    await _async_sleep(delay)
                    continue

                if error_class.fallbackable and has_next:
                    next_label = chain[idx + 1].label
                    logger.warning(
                        "resilient_genai[%s]: %s failed (%s) — falling back to %s",
                        label,
                        rung.label,
                        error_class.code,
                        next_label,
                    )
                    _emit(
                        MODEL_FALLBACK_EVENT,
                        {
                            "from_model": rung.label,
                            "to_model": next_label,
                            "code": error_class.code,
                            "provider": "gemini",
                        },
                    )
                    break  # next chain member

                raise ModelTurnError(error_class, rung.label) from exc

    # Chain exhausted (every rung fell back).
    if last_class is not None:
        raise ModelTurnError(last_class, last_model) from last_exc
    raise RuntimeError(f"resilient_genai[{label}]: empty failover chain for {model_ref!r}")  # pragma: no cover


__all__ = [
    "emit_stage_progress",
    "gemini_chain_for",
    "generate_content_resilient",
]
