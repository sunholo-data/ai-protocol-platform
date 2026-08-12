"""Platform-wide agent configuration (v6.14.0).

A single, admin-editable document that shapes EVERY skill's agent prompt. Today it
carries one thing: a platform ``preamble`` — shared identity / house-style /
guardrails that is PREPENDED to each skill's own instructions (see
``adk.platform_preamble_context``). It is the one place to say "you are part of
Aitana; here is how we behave" without editing every SKILL.md.

Stored as a singleton in the Firestore ``platform_config`` collection (doc id
``PLATFORM_CONFIG_DOC_ID``). Read on the hot request path via a TTL-cached loader
(``config.platform_config``), written only through the ``aitana-admin`` gated
admin route. When the doc is absent the loader returns a code default, so a fresh
env has a sensible preamble before any admin touches it.

The ``preamble`` is length-capped: it rides into every prompt on every turn, so an
unbounded value would bloat TTFT and token cost for the whole platform.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

# Singleton document id in the ``platform_config`` collection. There is exactly
# one platform-config doc; the id is fixed so reads/writes always target it.
PLATFORM_CONFIG_DOC_ID = "singleton"

# Hard cap on the preamble length. It is prepended to every agent instruction on
# every turn, so this bounds the per-turn prompt overhead platform-wide. Generous
# enough for a real house-style block, small enough to keep TTFT sane.
PREAMBLE_MAX_LEN = 20_000

# Cap on an admin-authored summariser prompt. Sent once per compaction (not per
# turn), so it can be far more generous than the preamble — but still bounded, on
# the Trap-22 lesson that an unvalidated admin write can take down a read path.
SUMMARIZER_PROMPT_MAX_LEN = 20_000

# The placeholder ADK's summarizer formats the conversation into. A prompt
# without it raises inside `str.format` DURING a user's turn, so it is validated
# at write time AND defended at read time.
CONVERSATION_PLACEHOLDER = "{conversation_history}"


class CompactionSettings(BaseModel):
    """Runtime overrides for conversation compaction (v6.23.0, doc 1b).

    Every field is optional and ``None`` means "use the coded default" — the
    per-model tuning table in ``adk/session.py`` for the thresholds, the shipped
    fidelity prompt for the summariser, the deployment env for the second pass.
    So an untouched config is a transparent no-op, and clearing a field in the
    admin panel restores shipped behaviour rather than writing a zero.

    These change ANSWER QUALITY silently — compaction's whole hazard is that its
    effects are invisible (a degraded answer looks identical to a good one), which
    is why the admin surface carries a warning and every value is echoed in the
    `HISTORY_COMPACTED` event.
    """

    # None → compaction behaves as coded. False → the token trigger is disabled
    # for every skill (the sliding-window backstop still applies; ADK owns that).
    enabled: bool | None = None

    # Prompt-token pressure that triggers a compaction. `gt=0` mirrors ADK's own
    # validator, so an invalid value is rejected at write time rather than
    # raising inside a turn.
    token_threshold: int | None = Field(default=None, gt=0, alias="tokenThreshold")

    # Raw events kept verbatim. NOTE the non-obvious second effect (findings log
    # §1): this also GATES whether compaction fires at all — candidates must
    # EXCEED it. The admin UI says so inline, because it produced a wrong test
    # result before it was understood.
    event_retention_size: int | None = Field(default=None, ge=0, alias="eventRetentionSize")

    # Tier ("pro"/"lite") or registry id. Never a raw api name — `entry_for()`
    # returns None for those and the chain silently takes a fallback (trap 8).
    summarizer_model: str | None = Field(default=None, alias="summarizerModel")

    # Must contain CONVERSATION_PLACEHOLDER. The most interesting lever in the
    # set: what the summariser is TOLD to preserve determines what survives far
    # more than any threshold does.
    summarizer_prompt: str | None = Field(default=None, max_length=SUMMARIZER_PROMPT_MAX_LEN, alias="summarizerPrompt")

    # Second pass (doc 1e). POLICY lives here; ADDRESSING (queue path, OIDC SA,
    # target URL) stays in env vars — an env without a queue provisioned cannot
    # be switched on from the admin panel by accident, and an env with one can be
    # flipped without a deploy.
    second_pass_enabled: bool | None = Field(default=None, alias="secondPassEnabled")
    second_pass_idle_seconds: int | None = Field(default=None, gt=0, alias="secondPassIdleSeconds")

    model_config = {"populate_by_name": True}


class PlatformConfig(BaseModel):
    """The platform-wide config singleton (``platform_config/singleton``).

    ``preamble`` is prepended to every skill's instructions when ``enabled`` is
    true and the text is non-empty; otherwise the wrapper is a transparent no-op.
    ``updated_by`` / ``updated_at`` are stamped server-side on each admin write
    for the audit trail (the append-only ``admin_audit`` collection keeps the
    full before/after history).
    """

    preamble: str = Field(default="", max_length=PREAMBLE_MAX_LEN)
    enabled: bool = True
    # Absent on every doc written before v6.23.0, hence a default factory rather
    # than a required field — an old doc must keep loading.
    compaction: CompactionSettings = Field(default_factory=CompactionSettings)
    updated_by: str = Field(default="", alias="updatedBy")
    updated_at: float = Field(default_factory=time.time, alias="updatedAt")

    model_config = {"populate_by_name": True}


__all__ = [
    "CONVERSATION_PLACEHOLDER",
    "PLATFORM_CONFIG_DOC_ID",
    "PREAMBLE_MAX_LEN",
    "SUMMARIZER_PROMPT_MAX_LEN",
    "CompactionSettings",
    "PlatformConfig",
]
