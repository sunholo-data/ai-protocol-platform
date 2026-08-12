"""Effective compaction settings — the read side of the tuning console (1b).

One place answers "what is actually in effect", for every consumer: the
before-agent callback (thresholds), the summarizer factory (model + prompt),
and the second-pass enqueue (policy). Precedence, highest first:

    admin settings (Firestore, 60s TTL cache)  →  env var  →  coded default

Two rules this module exists to enforce:

1. **Never fail a turn on bad config.** Compaction runs inside a user's request.
   An invalid stored value logs loudly and falls back to the coded default —
   the Trap-22 lesson (`gotcha_skill_list_500_blank_switcher`): one unvalidated
   admin write took down a whole list endpoint. Validation at the admin route is
   the first gate; this is the second, because a doc can be written by an older
   build, a script, or a hand edit in the console.

2. **Policy here, addressing in env.** The second pass's queue path, OIDC SA and
   target URL stay env-only, so an env with no queue provisioned cannot be
   switched on from the admin panel by accident — while an env that HAS one can
   be flipped without a deploy.
"""

from __future__ import annotations

import logging
import os

from db.models import CONVERSATION_PLACEHOLDER, CompactionSettings

logger = logging.getLogger(__name__)

_EMPTY = CompactionSettings()


def get_compaction_settings() -> CompactionSettings:
    """Admin-configured compaction settings, or empty (= all coded defaults).

    Fail-open: any read or validation problem yields empty settings, i.e. the
    behaviour shipped in code.
    """
    try:
        from config.platform_config import get_platform_config

        return get_platform_config().compaction or _EMPTY
    except Exception as exc:
        logger.warning("compaction settings unavailable (%s) — using coded defaults", type(exc).__name__)
        return _EMPTY


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def compaction_enabled() -> bool:
    """False disables the TOKEN trigger platform-wide (ADK's sliding-window
    backstop is App-level and unaffected — see the tuning-console inventory)."""
    value = get_compaction_settings().enabled
    return True if value is None else value


def summarizer_model_ref(default: str = "pro") -> str:
    """Tier or registry id for the summariser.

    A raw api name is rejected here rather than passed on: `entry_for()` returns
    None for those by design, so the chain would silently take a fallback and the
    admin would see no effect (findings log trap 8).
    """
    ref = (get_compaction_settings().summarizer_model or "").strip()
    if not ref:
        return default
    if "/" in ref or ref.count("-") > 4:
        logger.warning("compaction summarizer_model=%r looks like a raw api name — using %r", ref, default)
        return default
    return ref


def summarizer_prompt(default: str) -> str:
    """The admin prompt if it is usable, else the shipped one.

    A prompt without the placeholder raises inside `str.format` DURING a user's
    turn, so it is checked here even though the admin route rejects it too.
    """
    prompt = get_compaction_settings().summarizer_prompt
    if not prompt or not prompt.strip():
        return default
    if CONVERSATION_PLACEHOLDER not in prompt:
        logger.warning(
            "compaction summarizer_prompt is missing %s — ignoring it and using the shipped prompt",
            CONVERSATION_PLACEHOLDER,
        )
        return default
    return prompt


def second_pass_enabled() -> bool:
    """Policy: admin toggle wins; env var is the deployment default.

    So test can ship with the addressing wired and the feature off, then be
    switched on for a real conversation without a redeploy.
    """
    value = get_compaction_settings().second_pass_enabled
    if value is None:
        return _env_flag("COMPACTION_SECOND_PASS_ENABLED")
    return value


def second_pass_idle_seconds(default: int) -> int:
    value = get_compaction_settings().second_pass_idle_seconds
    if value is None or value <= 0:
        return default
    return value


def apply_threshold_overrides(config, *, where: str = ""):
    """Return a COPY of an `EventsCompactionConfig` with admin overrides applied.

    A copy, never a mutation: ADK's own code mutates these objects in place and
    ours are shared (module-level in `adk/session.py`; `from_app` shallow-copies
    the App), so an in-place edit here would leak one request's experiment into
    every other session in the container (findings log trap 5).

    Returns `config` unchanged when nothing is overridden or anything goes wrong.
    """
    if config is None:
        return config
    try:
        settings = get_compaction_settings()
        overrides: dict[str, int] = {}
        if settings.token_threshold is not None:
            overrides["token_threshold"] = settings.token_threshold
        if settings.event_retention_size is not None:
            overrides["event_retention_size"] = settings.event_retention_size
        if not overrides:
            return config
        # ADK's validator rejects one without the other; carry the existing value
        # across so a half-configured admin edit can't produce an invalid config.
        if "token_threshold" in overrides and "event_retention_size" not in overrides:
            existing = getattr(config, "event_retention_size", None)
            if existing is None:
                logger.warning("compaction: token_threshold override needs event_retention_size — ignoring both")
                return config
        if "event_retention_size" in overrides and "token_threshold" not in overrides:
            if getattr(config, "token_threshold", None) is None:
                logger.warning("compaction: event_retention_size override needs token_threshold — ignoring both")
                return config
        updated = config.model_copy(update=overrides)
        logger.debug("compaction overrides applied%s: %s", f" ({where})" if where else "", overrides)
        return updated
    except Exception as exc:
        logger.warning("compaction overrides not applied (%s) — using coded config", type(exc).__name__)
        return config


__all__ = [
    "apply_threshold_overrides",
    "compaction_enabled",
    "get_compaction_settings",
    "second_pass_enabled",
    "second_pass_idle_seconds",
    "summarizer_model_ref",
    "summarizer_prompt",
]
