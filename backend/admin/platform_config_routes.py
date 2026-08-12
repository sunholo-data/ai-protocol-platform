"""Platform-config admin plane (v6.14.0).

The platform preamble is prepended to EVERY skill's agent prompt, so editing it
is a platform-admin-only, audited action:

  * ``GET /api/admin/platform-config`` — read the current config (preamble text,
    enabled flag, last-edit metadata).
  * ``PUT /api/admin/platform-config`` — update the preamble and/or enabled flag.

``aitana-admin`` gated (deny-by-default) via the shared ``PlatformScope`` dependency
(the platform preamble is global — no single tenant may own it), and
every write records a before/after in the append-only ``admin_audit`` collection.
Validation (length cap) is enforced by the ``PlatformConfig`` model on the write
path in ``config.platform_config.update_platform_config`` — an over-cap preamble
is rejected with a 422 rather than landing in the store.

See docs/design/v6.14.0/platform-preamble.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.audit import record_admin_action
from admin.scope import PlatformScope
from config.platform_config import get_platform_config, update_platform_config
from db.models import CONVERSATION_PLACEHOLDER, PREAMBLE_MAX_LEN, CompactionSettings, PlatformConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/platform-config", tags=["admin-platform-config"])


class PlatformConfigUpdate(BaseModel):
    """Editable fields. Both optional so a caller can toggle ``enabled`` without
    resending the whole preamble, or edit the text without touching the flag.
    ``updated_by`` / ``updated_at`` are stamped server-side, never accepted here."""

    preamble: str | None = Field(default=None, max_length=PREAMBLE_MAX_LEN)
    enabled: bool | None = None
    # Compaction tuning (1b). Sent as a whole block — its own fields are each
    # optional-and-nullable, so an admin CLEARS a lever by sending null (restore
    # the coded default) rather than by omitting it.
    compaction: CompactionSettings | None = None


class CompactionDefaults(BaseModel):
    """The SHIPPED values each compaction lever falls back to when unset.

    Served so the admin UI can pre-fill the editor with the real prompt rather
    than an empty box — you cannot sensibly tune a prompt you cannot see. Kept
    OUT of ``PlatformConfig`` deliberately: that model is what gets persisted,
    and baking defaults into the stored doc would freeze a copy that stops
    tracking anything we ship later.
    """

    summarizer_prompt: str = Field(alias="summarizerPrompt")
    summarizer_model: str = Field(alias="summarizerModel")
    second_pass_idle_seconds: int = Field(alias="secondPassIdleSeconds")

    model_config = {"populate_by_name": True}


@router.get("/defaults", response_model=CompactionDefaults)
def read_compaction_defaults(scope: PlatformScope) -> CompactionDefaults:
    """Shipped compaction defaults (read-only). Aitana-admin only.

    Admin-gated because the summariser prompt is internal engineering detail —
    it describes exactly what we preserve from customer conversations.
    """
    from adk.compaction_summarizer import FIDELITY_PROMPT_TEMPLATE
    from internal_tasks.enqueue import _DEFAULT_IDLE_SECS

    return CompactionDefaults(
        summarizerPrompt=FIDELITY_PROMPT_TEMPLATE,
        summarizerModel="pro",
        secondPassIdleSeconds=_DEFAULT_IDLE_SECS,
    )


@router.get("", response_model=PlatformConfig)
def read_platform_config(scope: PlatformScope) -> PlatformConfig:
    """Return the current platform config. Aitana-admin only."""
    config = get_platform_config()
    log.info("admin.platform_config: read by uid=%s", scope.user.uid)
    return config


@router.put("", response_model=PlatformConfig)
def write_platform_config(body: PlatformConfigUpdate, scope: PlatformScope) -> PlatformConfig:
    """Update the platform preamble / enabled flag. Aitana-admin only, audited."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422, detail="Nothing to update: provide `preamble`, `enabled` and/or `compaction`."
        )

    if body.compaction is not None:
        # Reject at WRITE time what would otherwise raise inside a user's turn:
        # `str.format` on a prompt without the placeholder blows up DURING
        # compaction. The read path defends too (compaction_settings), but a
        # silent fallback there would leave an admin staring at a saved value
        # that does nothing — say no here instead.
        prompt = body.compaction.summarizer_prompt
        if prompt and CONVERSATION_PLACEHOLDER not in prompt:
            raise HTTPException(
                status_code=422,
                detail=f"summarizer_prompt must contain {CONVERSATION_PLACEHOLDER} — "
                "it is where the conversation is substituted in.",
            )
        # Nulls mean "clear this lever", so the block is stored whole rather than
        # merged field-by-field (exclude_none would silently drop a clear).
        updates["compaction"] = body.compaction.model_dump(by_alias=True)

    before = get_platform_config().model_dump(by_alias=True)
    try:
        config = update_platform_config(updates, updated_by=scope.user.uid)
    except ValueError as exc:  # model validation (e.g. over-cap preamble)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="update_platform_config",
        target="singleton",
        before=before,
        after=config.model_dump(by_alias=True),
    )
    log.info(
        "admin.platform_config: updated by uid=%s (enabled=%s, preamble_len=%d)",
        scope.user.uid,
        config.enabled,
        len(config.preamble),
    )
    return config


__all__ = ["router"]
