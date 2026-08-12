"""Shared gate logic for the A2UI surface-action endpoints.

Two endpoints share these helpers:

  * ``a2ui_surface_action_routes`` (sprint 2.10) — the original
    fire-and-forget action persistence endpoint.
  * ``a2ui_surface_action_run_routes`` (ACTION-TRIGGER M1) — the bundled
    write-and-run endpoint that, in addition to the action write, kicks
    off an agent turn and streams AG-UI events back.

Pulling the gate primitives here keeps both endpoints honouring exactly
the same access policy. Behaviour-preserving refactor — see
docs/design/v6.1.0/action-triggered-agent-turn.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException

from auth import User
from db.chat_sessions import get_session_index
from db.models.chat_session import ChatSessionIndex
from skills import skill_config

log = logging.getLogger(__name__)

# Hard cap on action.context size. 4 KB matches the iframe-context
# limit — same threat model, same trade-off.
_MAX_CONTEXT_BYTES = 4096

# Session-state key namespace. Must match the prefix in
# adk/a2ui_surface_context.py — both ends anchor on this string.
_STATE_KEY_NAMESPACE = "a2ui_surface_context"


def _require_session(session_id: str) -> ChatSessionIndex:
    """Gate 2: the session must exist in the index. 404 if not.

    Raises:
        HTTPException(404): when the session id is unknown.
    """
    idx = get_session_index(session_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return idx


def _delegate_with_grant(skill: Any, user: User, flag: str) -> Any | None:
    """First ACCESS-FILTERED delegate of ``skill`` whose a2ui config sets ``flag``.

    Handoffs from a front door are TRANSPARENT (``transfer_to_agent``), so a
    DELEGATE's tool can render a surface / elicitation form in the DOOR's chat —
    and the follow-up request (surface-action, surface-data, surface-action-run)
    is then made against the DOOR's skill id. The grant lives on the delegate that
    produced the surface, not on the skill these gates inspect, so a door fronting
    correctly-opted-in specialists gets wrongly denied (live 2026-07-21: ONE
    Assistant → one-ppa-expert's entsoe_day_ahead_prices form 403'd on Submit).

    Deny-by-default is preserved:
      * the delegate set is the same access-filtered set ``create_agent`` builds,
        so a delegate the user cannot reach can never unlock the door;
      * the grant is read off a SINGLE delegate (never one condition from the door
        and another from a delegate);
      * direct delegates only (depth 1) — what a transparent handoff reaches;
      * any resolution error returns None, so the caller falls through to its 403
        (fails CLOSED, never opens the gate on an error path).
    """
    from adk.agent import accessible_delegate_rules
    from auth.access_context import build_access_context

    try:
        rules = accessible_delegate_rules(skill, build_access_context(user))
    except Exception as exc:  # never open a gate because resolution broke
        log.warning("a2ui gate: delegate resolution failed (%s) — denying", type(exc).__name__)
        return None
    for delegate, _rule in rules:
        cfg = (delegate.skill_metadata.tool_configs or {}).get("a2ui") or {}
        if cfg.get(flag):
            return delegate
    return None


def _enforce_skill_opt_in(skill_id: str, user: User) -> None:
    """Enforce gates 4 + 5 + 6: the skill must exist, have an a2ui
    tool_config, AND explicitly opt into surface-context writes.

    Raises:
        HTTPException(403): on any of the three sub-gate failures.
    """
    # Alias-tolerant (CLAUDE.md #9): the caller may hold a slug rather than the
    # canonical id — the Skill Studio mounts its copilot by slug — and a raw
    # id lookup turned that into a misleading 403 "Access denied".
    skill = skill_config.get_skill(skill_id) or skill_config.resolve_skill_ref(skill_id, getattr(user, "uid", None))
    if skill is None:
        log.info(
            "surface_action: skill not found uid=%s skill_id=%s session deleted?",
            user.uid,
            skill_id,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    a2ui_config = (skill.skill_metadata.tool_configs or {}).get("a2ui") or {}
    if a2ui_config.get("allow_surface_context_writes"):
        return

    # Delegation-aware fallback — the surface being acted on may have been
    # rendered by a delegate during a transparent handoff. See _delegate_with_grant
    # for why this preserves deny-by-default.
    delegate = _delegate_with_grant(skill, user, "allow_surface_context_writes")
    if delegate is not None:
        log.info(
            "surface_action: allowed via opted-in delegate uid=%s skill_id=%s delegate=%s",
            user.uid,
            skill_id,
            delegate.skill_id,
        )
        return

    if not a2ui_config:
        log.info(
            "surface_action: skill has no a2ui tool_config uid=%s skill_id=%s",
            user.uid,
            skill_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Skill '{skill_id}' has no A2UI tool_config — surface actions "
                f"are only accepted from A2UI-enabled skills"
            ),
        )

    log.info(
        "surface_action: skill not opted into surface context writes uid=%s skill_id=%s",
        user.uid,
        skill_id,
    )
    raise HTTPException(
        status_code=403,
        detail=(
            f"Skill '{skill_id}' has not opted into A2UI surface context "
            f"writes (set tool_configs.a2ui.allow_surface_context_writes: true)"
        ),
    )


def _enforce_size_cap(context: dict[str, Any] | None) -> str:
    """Gate 7: serialize action.context and reject if it's larger than
    the cap. Returns the serialized bytes count for logging.

    Raises:
        HTTPException(400): when context is not JSON-serializable.
        HTTPException(413): when serialized context exceeds the cap.
    """
    if context is None:
        return "0"
    try:
        serialized = json.dumps(context, default=str)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"action.context is not JSON-serializable: {exc}",
        ) from exc
    size = len(serialized.encode("utf-8"))
    if size > _MAX_CONTEXT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"action.context is {size} bytes; max is {_MAX_CONTEXT_BYTES}",
        )
    return str(size)


__all__ = [
    "_MAX_CONTEXT_BYTES",
    "_STATE_KEY_NAMESPACE",
    "_enforce_size_cap",
    "_enforce_skill_opt_in",
    "_require_session",
]
