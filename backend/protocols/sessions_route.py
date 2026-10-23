"""REST API for ChatSessionIndex management.

Endpoints (all authenticated):
  GET    /api/documents/{docId}/sessions   list sessions for a document
  GET    /api/sessions/{sessionId}         get one session's metadata
  PATCH  /api/sessions/{sessionId}         rename / re-scope / archive (owner)
  DELETE /api/sessions/{sessionId}         soft-delete (owner)

No fork endpoint — deferred to v6.1 (no channel consumers yet).
No idempotency ledger — deferred to v6.1.

Non-owner reads return 403 rather than 404 for sessions (unlike skills
which use 404 to avoid leaking existence). Chat session IDs come from
Agent Engine and are not guessable from the outside.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from opentelemetry import trace
from pydantic import BaseModel

from adk.agui import APP_NAME
from adk.callbacks import A2UI_SURFACE_STATE_PREFIX
from adk.notability import tool_tier
from adk.session import get_session_service
from auth import User, get_current_user
from db.chat_sessions import (
    SessionFilter,
    get_session_index,
    list_sessions_for_document,
    list_sessions_for_skill,
    list_sessions_shared_with,
    most_recent_session_for_user,
    soft_delete_session,
    update_session_fields,
)
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex

router = APIRouter(prefix="/api", tags=["sessions"])
log = logging.getLogger(__name__)


def _state_to_a2ui_surfaces(state: Any) -> list[dict[str, Any]]:
    """Extract persisted A2UI workbench surfaces from session state (7.5 M3).

    Reads session-scoped ``a2ui_surface:*`` keys the result emitter stashes and
    returns their parsed payloads ordered by ``createdAt`` (first-seen order), so
    the frontend can replay them into the SurfaceRegistry on resume and the
    workbench (per-result tabs + index) returns without re-running any tool.
    Fail-open: an unparseable entry is skipped, never 500s the history load.

    When a stash entry carries a ``clientDataModel`` block (a client-side edit
    persisted via ``POST /surface-data`` — e.g. an obligation what-if scenario),
    it is materialised as one extra ``updateDataModel`` message appended AFTER
    the canonical tool-emitted messages, so the ordinary replay path restores
    the edited data model with no frontend changes.
    """
    if not state:
        return []
    surfaces: list[dict[str, Any]] = []
    for key, raw in dict(state).items():
        if not isinstance(key, str) or not key.startswith(A2UI_SURFACE_STATE_PREFIX):
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(payload, dict) and payload.get("surfaceId") and payload.get("messages"):
                surfaces.append(_apply_client_data_model(payload))
        except (ValueError, TypeError) as exc:
            log.warning("a2ui surface rehydrate: skipping unparseable state key %s: %s", key, exc)
    surfaces.sort(key=lambda s: s.get("createdAt") or 0)
    return surfaces


def _apply_client_data_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Append the persisted client data-model edit as an ``updateDataModel``
    message (root-value replace — the same shape the frontend mirror writes).
    Copies rather than mutates: in-memory session services hand back the live
    state dict."""
    client_dm = payload.get("clientDataModel")
    if not isinstance(client_dm, dict) or not isinstance(client_dm.get("value"), dict):
        return payload
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    return {
        **payload,
        "messages": [
            *messages,
            {
                "version": "v0.9",
                "updateDataModel": {"surfaceId": payload["surfaceId"], "value": client_dm["value"]},
            },
        ],
    }


def get_messages_session_service():
    """Return the shared session service singleton for reading message history."""
    return get_session_service()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: float
    # v6.11.0 — the producing agent's avatar + display name, resolved from the
    # event author, so a RESUMED transcript shows per-delegate marks (matching
    # the live per-delegate avatars). None for the root skill / user (frontend
    # falls back to the session skill / brand mark).
    avatar: str | None = None
    agent_label: str | None = None
    # Stable per-message id (the ADK event id): act on a message exactly once.
    id: str | None = None
    # Who sent a USER message — from the server's per-turn record of the
    # AUTHENTICATED caller (db.chat_sessions.record_turn_author), never from the
    # text: {uid, email, name, role: owner|collaborator, authMode}. None for
    # assistant messages and for turns from before authors were recorded. A
    # collaborator's message text still starts "[name] " for the model — trust
    # this field, not that prefix.
    author: dict[str, str] | None = None
    # The turn a message belongs to: a user message and the replies to it share it.
    turn_id: str | None = None


class GetSessionMessagesResponse(BaseModel):
    messages: list[ChatMessage]
    session_id: str
    # Who may post in this thread besides its owner (a machine's supervisors) —
    # shown to the owner and to collaborators.
    collaborators: list[str] = []
    # Persisted A2UI workbench surfaces (7.5 M3 resume rehydration). Each entry
    # is the same payload the live AG-UI CUSTOM `A2UI_SURFACE` event carries
    # ({surfaceId, messages, artifact, sourceId, toolName, createdAt}), read from
    # session-scoped `a2ui_surface:*` state and ordered by createdAt. The frontend
    # replays them into the SurfaceRegistry on resume so the workbench (per-result
    # tabs + index) returns without re-running any tool. Empty for legacy sessions.
    a2ui_surfaces: list[dict[str, Any]] = []
    # True when the Firestore index says this conversation had turns but the
    # ADK/Vertex session holding the transcript is gone. Without this the route
    # returns 200 + an empty list and the UI renders an indistinguishable blank
    # chat — the user picks yesterday's conversation and simply gets nothing,
    # with no error to explain it (CLAUDE.md #8, NEVER SILENT). The frontend
    # renders a notice instead of an empty thread.
    transcript_unavailable: bool = False


class ToolActivity(BaseModel):
    """A historical tool call reconstructed from ADK session events, in the
    shape the frontend Activity panel's `ToolCallState` expects."""

    id: str
    name: str
    status: str  # "success" | "error"
    ts: float  # epoch ms
    argsJson: str | None = None
    resultContent: str | None = None
    # Curated-workbench tier (v6.11.0): "internal" | "notable" | "artifact".
    # Decided backend-side from the result→A2UI registry so the Home digest and
    # every channel curate identically (Axiom #7/#10). Never an auth boundary.
    notability: str = "internal"


class DelegationActivity(BaseModel):
    """A historical skill→skill delegation reconstructed from ADK events, in
    the shape the frontend's `DelegationMarkerItem` expects."""

    id: str
    target: str
    targetDisplay: str
    mode: str  # "auto" | "suggest"
    ts: float  # epoch ms
    # Delegations are user-notable (the "brought in the Contract Expert" story).
    notability: str = "notable"


class GetSessionActivityResponse(BaseModel):
    tool_calls: list[ToolActivity]
    delegations: list[DelegationActivity]
    # Earliest event timestamp (epoch ms) — the real session start, for the
    # Activity "Session started" marker. None when the session has no events.
    session_start_ts: float | None
    session_id: str


class ChatSessionSummary(BaseModel):
    session_id: str
    document_ids: list[str]
    skill_id: str
    owner_uid: str
    access_control: dict[str, Any]
    title: str | None
    turn_count: int
    first_message_at: str
    last_message_at: str
    archived_at: str | None
    is_owner: bool
    can_fork: bool
    # Friendly name of the skill this session belongs to, for the cross-skill
    # history list (CLAUDE.md #9 — a human must never be asked to tell
    # conversations apart by UUID). None when the skill can't be resolved; the
    # frontend falls back to no chip rather than printing the id.
    skill_label: str | None = None
    # v6.23.0 B5 Phase 2 — the canonical transcript for this session is gone, so
    # opening it shows "messages no longer available". Surfaced in the LIST so a
    # user learns that before clicking rather than after. Set by
    # `aiplatform sessions reconcile --mark-lost`; see ChatSessionIndex.
    transcript_lost: bool = False
    # Shared machine threads: the owner's friendly name (so a "Shared with me"
    # row says whose thread it is) and whether the viewer may reply in it.
    owner_name: str | None = None
    can_reply: bool = False
    # Projects (projects.md §5): explicit membership, so a chat header / row can
    # show which project the chat is in. Derived membership is not listed here.
    project_id: str | None = None


class ListSessionsResponse(BaseModel):
    sessions: list[ChatSessionSummary]
    next_cursor: str | None


class GetSessionResponse(BaseModel):
    session: ChatSessionSummary


class PatchSessionRequest(BaseModel):
    title: str | None = None
    access_control: dict[str, Any] | None = None
    archived: bool | None = None
    # Move a chat into (an id) or out of (null) one of the caller's projects.
    # Absent leaves it as it is — hence the model_fields_set check in the route.
    project_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_name(owner_uid: str, cache: dict[str, str | None] | None = None) -> str | None:
    """Friendly name for a session owner (CLAUDE.md #9), or None if unknown."""
    if cache is not None and owner_uid in cache:
        return cache[owner_uid]
    name: str | None = None
    try:
        from auth.firebase_auth import resolve_user_by_uid

        owner = resolve_user_by_uid(owner_uid) if owner_uid else None
        if owner is not None:
            name = owner.display_name or owner.email or None
    except Exception as exc:
        log.debug("owner name lookup failed for %s: %s", owner_uid, exc)
    if cache is not None:
        cache[owner_uid] = name
    return name


def _to_summary(
    idx: ChatSessionIndex,
    viewer_uid: str,
    skill_label: str | None = None,
    viewer_email: str = "",
    owner_name: str | None = None,
) -> ChatSessionSummary:
    is_owner = idx.owner_uid == viewer_uid
    email = (viewer_email or "").strip().lower()
    return ChatSessionSummary(
        owner_name=owner_name,
        can_reply=is_owner or (bool(email) and email in (idx.collaborators or [])),
        skill_label=skill_label,
        session_id=idx.session_id,
        document_ids=list(idx.document_ids),
        skill_id=idx.skill_id,
        owner_uid=idx.owner_uid,
        access_control=idx.access_control.model_dump(exclude_none=True),
        title=idx.title,
        turn_count=idx.turn_count,
        first_message_at=idx.first_message_at.isoformat(),
        last_message_at=idx.last_message_at.isoformat(),
        archived_at=idx.archived_at.isoformat() if idx.archived_at else None,
        is_owner=is_owner,
        can_fork=(idx.archived_at is None),
        transcript_lost=idx.transcript_lost,
        project_id=idx.project_id if is_owner else None,
    )


def resolve_skill_labels(skill_ids: list[str]) -> dict[str, str]:
    """Map skill_id → friendly display name, one lookup per DISTINCT id.

    Used by the cross-skill history list so each row can say which agent it was
    with. Fail-soft per id: a skill that has been deleted or is unreadable is
    simply omitted, and the row renders without a chip rather than falling back
    to printing a UUID at a human (CLAUDE.md #9).
    """
    from skills.skill_config import get_skill

    labels: dict[str, str] = {}
    for sid in {s for s in skill_ids if s}:
        try:
            skill = get_skill(sid)
        except Exception as exc:
            log.warning("skill label lookup failed for %s: %s", sid, exc)
            continue
        label = _skill_label(skill) if skill else None
        if label:
            labels[sid] = label
    return labels


def _author_agent_map(idx: ChatSessionIndex, ctx: Any) -> dict[str, dict[str, str | None]]:
    """Map an ADK event ``author`` (agent name) → the skill's {avatar, label}.

    Covers the session's root skill and its accessible delegates, keyed by BOTH
    naming schemes the author can take: the sanitized-uuid name
    (``_safe_agent_name``, used for the root + legacy delegates) and the slug
    name (``_delegate_agent_name``, v6.10 unified handoff). Used to attribute a
    resumed transcript's messages to the agent that produced them. Fail-open:
    any resolution error returns an empty map (frontend falls back to the root).
    """
    out: dict[str, dict[str, str | None]] = {}
    # The resident is not a skill, so the skill walk below can never name it —
    # and its messages are the ones that most need naming, because M5a delivers
    # them UNPROMPTED, minutes after the turn the user was watching. Without an
    # entry a restart notice or a late answer renders as a bare assistant
    # bubble with no provenance at all: the reader cannot tell what spoke, or
    # why it spoke now. Seeded before the try, so a skill-resolution failure
    # still leaves the resident attributed.
    from internal_tasks.resident_routes import RESIDENT_AUTHOR

    out[RESIDENT_AUTHOR] = {"avatar": None, "label": "Resident agent"}
    try:
        from adk.agent import _delegate_agent_name, _safe_agent_name, accessible_delegate_rules
        from skills import skill_config

        def _add(skill: Any) -> None:
            meta = {"avatar": (getattr(skill, "avatar", "") or None), "label": _skill_label(skill)}
            out[_safe_agent_name(skill.skill_id)] = meta
            taken: set[str] = set()
            out[_delegate_agent_name(skill, taken)] = meta

        root = skill_config.get_skill(idx.skill_id)
        if root is None:
            return out
        _add(root)
        for delegate, _rule in accessible_delegate_rules(root, ctx):
            _add(delegate)
    except Exception as exc:
        log.warning("author→agent map build failed (suppressed): %s", exc)
    return out


def _skill_label(skill: Any) -> str | None:
    return (getattr(skill, "display_name", "") or getattr(skill, "name", "")) or None


def _events_to_messages(events: list, author_map: dict[str, dict[str, str | None]] | None = None) -> list[ChatMessage]:
    """Extract user/assistant text messages from ADK session events.

    Skips events with no content (tool calls, system events, empty turns).
    Joins multi-part content parts with a space. When ``author_map`` is given,
    attributes each assistant message to the agent that produced it (``e.author``)
    → its avatar + display name (6.11 resume per-delegate marks).
    """
    author_map = author_map or {}
    messages: list[ChatMessage] = []
    for e in events:
        if not e.content or not e.content.parts:
            continue
        # Thinking parts (`thought=True`) are the model's private reasoning, not
        # its reply: a transcript reader (a person resuming, or an agent such as
        # Daneel acting on the answer) must not take them as what was said.
        text = " ".join(p.text for p in e.content.parts if p.text and getattr(p, "thought", None) is not True).strip()
        if not text:
            continue
        is_assistant = e.author != "user"
        role: Literal["user", "assistant"] = "assistant" if is_assistant else "user"
        agent = author_map.get(e.author) if is_assistant else None
        messages.append(
            ChatMessage(
                role=role,
                # epoch MS (ADK events are unix seconds) so the frontend can
                # render each history bubble at its ORIGINAL send time, not the
                # load time. Matches session_start_ms units.
                timestamp=_to_epoch_ms(e.timestamp),
                content=text,
                avatar=(agent or {}).get("avatar"),
                agent_label=(agent or {}).get("label"),
                id=eid if isinstance(eid := getattr(e, "id", None), str) and eid else None,
            )
        )
    return messages


_AUTHOR_SKEW_MS = 2000  # the record is written just before the turn's user event


def _join_turn_authors(messages: list[ChatMessage], records: list[dict]) -> list[ChatMessage]:
    """Attach each user message's recorded author and turn id (in place).

    Records are written when a turn is ACCEPTED, so each user message takes the
    latest unused record at or before it — a turn that failed before its
    message was saved leaves a record that's simply skipped. Assistant messages
    inherit the turn id of the user message they answer.
    """
    i, current = 0, None
    for m in messages:
        if m.role == "user":
            match = None
            while i < len(records) and float(records[i].get("at") or 0) <= m.timestamp + _AUTHOR_SKEW_MS:
                match = records[i]
                i += 1
            if match is not None:
                m.author = {k: str(match.get(k) or "") for k in ("uid", "email", "name", "role", "authMode")}
                current = str(match.get("turnId") or "") or None
            else:
                current = None
            m.turn_id = current
        else:
            m.turn_id = current
    return messages


def _to_epoch_ms(ts: float) -> float:
    """ADK event timestamps are unix seconds; the frontend wants ms."""
    return ts * 1000 if ts and ts < 1e12 else ts


# Function-call names that are skill→skill delegations, not real tools:
#   transfer_to_agent — ADK auto sub-agent handoff (floor "auto")
#   request_handoff   — the confirm / confirm_with_fields tool (backend adk/agent.py,
#                       8.2). The interactive confirm renders as an A2UI chat form via
#                       the elicitation primitive; this history marker records that a
#                       handoff was proposed.
_DELEGATION_FN_NAMES = {"transfer_to_agent", "request_handoff"}


def _humanize(name: str) -> str:
    s = name.replace("_", " ").replace("-", " ").strip()
    return (s[:1].upper() + s[1:]) if s else name


def _earliest_event_ts(events: list) -> float | None:
    """Epoch-ms timestamp of the first event — the real session start."""
    stamps = [_to_epoch_ms(getattr(e, "timestamp", 0) or 0) for e in events if getattr(e, "timestamp", None)]
    return min(stamps) if stamps else None


def _events_to_delegations(events: list) -> list[DelegationActivity]:
    """Reconstruct skill→skill delegations from ADK events. Auto handoffs show
    up as `transfer_to_agent` function calls; confirm/confirm_with_fields handoffs
    as `request_handoff` calls. targetDisplay is a humanised fallback (the live
    AGENT_DELEGATION event carries the pretty name; history only has the id)."""
    out: list[DelegationActivity] = []
    for e in events:
        if not e.content or not e.content.parts:
            continue
        ts = _to_epoch_ms(getattr(e, "timestamp", 0) or 0)
        for p in e.content.parts:
            fc = getattr(p, "function_call", None)
            name = getattr(fc, "name", None) if fc is not None else None
            if name not in _DELEGATION_FN_NAMES:
                continue
            args = dict(fc.args) if getattr(fc, "args", None) else {}
            if name == "transfer_to_agent":
                target = str(args.get("agent_name") or args.get("agent") or "")
                mode = "auto"
            else:
                target = str(args.get("target_skill_id") or args.get("target") or "")
                mode = "suggest"
            if not target:
                continue
            cid = getattr(fc, "id", None) or f"{name}-{ts}-{len(out)}"
            out.append(DelegationActivity(id=cid, target=target, targetDisplay=_humanize(target), mode=mode, ts=ts))
    return out


def _events_to_tool_activity(events: list) -> list[ToolActivity]:
    """Reconstruct tool calls from ADK session events so the Activity panel
    can show a session's history after a reload (live stream state is gone).

    Pairs each ``function_call`` part with its matching ``function_response``
    (by call id). A call with no response is marked "error" (incomplete turn).
    Delegations aren't reconstructed here — they're live-only for now.
    """
    import json

    calls: dict[str, dict] = {}
    responses: dict[str, Any] = {}
    # v6.23.0 B5 Phase 2 — id-less responses, keyed by function NAME.
    #
    # Pairing was id-only, and a `function_response` whose `id` is None was
    # dropped on the floor. Its call then found no match and was rendered
    # `status="error"` — so a tool that ran and returned showed up RED in the
    # admin trace, sending an operator to debug a failure that never happened.
    # A call with no id had the same fate: it got a synthetic key that no
    # response could ever match.
    #
    # Falling back to the function name is a heuristic, so it is deliberately
    # FIFO: with two outstanding calls to the same tool, the first id-less
    # response pairs with the first call, which preserves ordering and cannot
    # pair a response with a call that came after it. Worst case it attributes a
    # result to the wrong one of two identical calls; that is strictly better
    # than reporting both as failed.
    responses_by_name: dict[str, list[Any]] = {}
    order: list[str] = []
    for e in events:
        if not e.content or not e.content.parts:
            continue
        ts = _to_epoch_ms(getattr(e, "timestamp", 0) or 0)
        for p in e.content.parts:
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "name", None) and fc.name not in _DELEGATION_FN_NAMES:
                cid = getattr(fc, "id", None) or f"{fc.name}-{ts}-{len(order)}"
                args = dict(fc.args) if getattr(fc, "args", None) else {}
                calls[cid] = {"id": cid, "name": fc.name, "args": args, "ts": ts}
                order.append(cid)
            fr = getattr(p, "function_response", None)
            if fr is not None:
                rid = getattr(fr, "id", None)
                if rid is not None:
                    responses[rid] = getattr(fr, "response", None)
                elif getattr(fr, "name", None):
                    responses_by_name.setdefault(fr.name, []).append(getattr(fr, "response", None))

    items: list[ToolActivity] = []
    for cid in order:
        c = calls[cid]
        has_resp = cid in responses
        resp = responses.get(cid)
        if not has_resp:
            queued = responses_by_name.get(c["name"])
            if queued:
                resp = queued.pop(0)
                has_resp = True
        items.append(
            ToolActivity(
                id=cid,
                name=c["name"],
                status="success" if has_resp else "error",
                ts=c["ts"],
                argsJson=(json.dumps(c["args"], ensure_ascii=False, default=str) if c["args"] else None),
                resultContent=(json.dumps(resp, ensure_ascii=False, default=str) if resp is not None else None),
                notability=tool_tier(c["name"]),
            )
        )
    return items


def _require_session(session_id: str) -> ChatSessionIndex:
    idx = get_session_index(session_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return idx


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/documents/{doc_id}/sessions", response_model=ListSessionsResponse)
async def list_document_sessions(
    doc_id: str,
    request: Request,
    filter: Annotated[SessionFilter, Query()] = "all",
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),  # noqa: B008
) -> ListSessionsResponse:
    """List non-archived sessions for a document, filtered by viewer access.

    - filter=mine: only sessions owned by the caller
    - filter=team: sessions the caller can see via tag intersection (not own)
    - filter=all:  union (default)

    Returns 200 with an empty list when the viewer has no accessible sessions
    (never 403 — the document itself may be accessible without any sessions).

    Note: there is no separate document-level access check here. ParsedDocument
    has no AccessControl block, so the gate is entirely at the session level:
    list_sessions_for_document filters results to sessions the caller can access.
    A caller supplying a foreign doc_id gets an empty list, not a 403.
    """
    ctx = request.state.access
    sessions, next_cursor = list_sessions_for_document(doc_id, ctx, filter=filter, page_size=page_size, cursor=cursor)
    return ListSessionsResponse(
        sessions=[_to_summary(s, ctx.uid) for s in sessions],
        next_cursor=next_cursor,
    )


class RecentSessionResponse(BaseModel):
    """Where to send a returning signed-in user (v6.5.0 AUTH-LANDING)."""

    session_id: str
    skill_id: str
    slug: str | None
    owner_id: str


@router.get("/sessions", response_model=ListSessionsResponse)
async def list_my_sessions(
    request: Request,
    skill_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=50),
    shared: bool = Query(default=False),
    user: User = Depends(get_current_user),  # noqa: B008
) -> ListSessionsResponse:
    """The caller's own conversations across ALL skills, newest first.

    ``shared=true`` lists instead the threads OTHERS own that name the caller
    as a collaborator (shared machine threads — e.g. an agent's threads shared
    with its supervisor), each with the owner's friendly name. ``skill_id`` is
    ignored there.

    Switching agent via the top bar starts a NEW session on the new skill, so a
    single sitting that moved between agents is split into one session per
    skill. The per-skill list at ``GET /api/skills/{id}/sessions`` therefore
    shows the user only the fragment belonging to wherever they are standing,
    which reads as "my conversation wasn't recorded" (reported 2026-08-05).
    This endpoint is the whole history.

    Each row carries ``skill_label`` — the friendly agent name — so the list can
    say which agent a conversation was with. Pass ``skill_id`` to filter to one
    agent; omit it for everything. Owner-scoped: only the caller's own sessions,
    never a cross-user view.
    """
    ctx = request.state.access
    if shared:
        sessions, next_cursor = list_sessions_shared_with(ctx.email, page_size=page_size, cursor=cursor)
        sessions = [s for s in sessions if s.owner_uid != ctx.uid]
    else:
        sessions, next_cursor = list_sessions_for_skill(
            skill_id=skill_id,
            owner_uid=ctx.uid,
            page_size=page_size,
            cursor=cursor,
        )
    labels = resolve_skill_labels([s.skill_id for s in sessions])
    names: dict[str, str | None] = {}
    return ListSessionsResponse(
        sessions=[
            _to_summary(
                s,
                ctx.uid,
                labels.get(s.skill_id),
                viewer_email=ctx.email,
                owner_name=_owner_name(s.owner_uid, names) if s.owner_uid != ctx.uid else None,
            )
            for s in sessions
        ],
        next_cursor=next_cursor,
    )


# NB: registered BEFORE `/sessions/{session_id}` so "recent" isn't captured as
# a session id.
@router.get("/sessions/recent", responses={204: {"description": "no qualifying session"}})
async def get_recent_session(
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
):
    """Most-recent non-archived session whose skill is still visible + enabled
    for the caller, or 204 when none qualify.

    Powers the homepage redirect: a returning user lands back in their last
    chat. Owner-scoped to the JWT uid; skips sessions whose skill was since
    hidden or removed from the tenant's `enabled_skills` so we never route a
    user into a chat they can no longer open.
    """
    # Function-local imports keep the protocols package import-light + avoid a
    # cycle with the skills package.
    from db.clients import resolve_enabled_skills
    from skills import skill_config

    ctx = request.state.access
    enabled = resolve_enabled_skills(user)
    allowed = set(enabled) if enabled is not None else None

    for idx in most_recent_session_for_user(user.uid, limit=10):
        skill = skill_config.get_skill(idx.skill_id)
        if skill is None or not ctx.can_access_skill(skill):
            continue
        if allowed is not None and (skill.slug is None or skill.slug not in allowed):
            continue
        trace.get_current_span().set_attribute("landing.outcome", "resumed")
        return RecentSessionResponse(
            session_id=idx.session_id,
            skill_id=skill.skill_id,
            slug=skill.slug,
            owner_id=skill.owner_id,
        )

    trace.get_current_span().set_attribute("landing.outcome", "no_session")
    return Response(status_code=204)


@router.get("/sessions/{session_id}", response_model=GetSessionResponse)
async def get_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> GetSessionResponse:
    """Return metadata for a single session.

    Returns 403 when the caller cannot access the session (session IDs are
    not guessable so 403 is safe here — no existence leak).
    """
    idx = _require_session(session_id)
    ctx = request.state.access
    if not ctx.can_access(idx):
        raise HTTPException(status_code=403, detail="Access denied")
    return GetSessionResponse(
        session=_to_summary(
            idx,
            ctx.uid,
            viewer_email=ctx.email,
            owner_name=_owner_name(idx.owner_uid) if idx.owner_uid != ctx.uid else None,
        )
    )


@router.patch("/sessions/{session_id}", response_model=GetSessionResponse)
async def patch_session(
    session_id: str,
    body: PatchSessionRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> GetSessionResponse:
    """Rename, re-scope, or archive a session. Owner-only."""
    idx = _require_session(session_id)
    ctx = request.state.access

    if not ctx.can_access(idx):
        raise HTTPException(status_code=403, detail="Access denied")
    if not ctx.is_owner(idx):
        raise HTTPException(status_code=403, detail="Only the session owner can modify it")

    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.access_control is not None:
        try:
            AccessControl.model_validate(body.access_control)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid accessControl: {exc}") from exc
        fields["accessControl"] = body.access_control
    if body.archived is True and idx.archived_at is None:
        from datetime import datetime

        fields["archivedAt"] = datetime.now(UTC).isoformat()
    elif body.archived is False:
        fields["archivedAt"] = None
    if "project_id" in body.model_fields_set:
        if body.project_id:
            from db.projects import get_project

            # The project must be the caller's own; anything else is "not found".
            if get_project(user.uid, body.project_id) is None:
                raise HTTPException(
                    status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found."}
                )
        fields["projectId"] = body.project_id or None

    if fields:
        update_session_fields(session_id, fields)

    updated = get_session_index(session_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found after update")
    return GetSessionResponse(session=_to_summary(updated, ctx.uid))


@router.get("/sessions/{session_id}/messages", response_model=GetSessionMessagesResponse)
async def get_session_messages(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> GetSessionMessagesResponse:
    """Return the full message history for a session.

    Access policy (chat-history-deep-fixes-2 / 1.15 Bug E): aligned with the
    metadata read at ``GET /api/sessions/{id}`` — the caller must
    ``ctx.can_access(idx)``. A non-owner with valid access (public, domain,
    same-tag, or specific-allow) reads the events Vertex stored under the
    OWNER's user_id; the route always queries Vertex with ``idx.owner_uid``
    regardless of caller. Sharing means reading the owner's events, not
    attributing them to the reader. PATCH and DELETE remain owner-only.

    Returns 403 (not 404) consistently — session IDs are random UUIDs, not
    guessable, so 403 is safe and avoids an existence-leak edge case.
    """
    idx = _require_session(session_id)
    ctx = request.state.access
    if not ctx.can_access(idx):
        raise HTTPException(status_code=403, detail="Access denied")

    session_service = get_messages_session_service()
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=idx.owner_uid,
        session_id=session_id,
    )
    events = session.events if session is not None else []
    state = session.state if session is not None else None

    # An index row that recorded turns, with no ADK session behind it, means the
    # transcript is unrecoverable — not that the conversation was empty. Log it
    # loudly (this is how we detect a recurrence) and tell the client, so it can
    # show a notice rather than a silently blank thread.
    transcript_unavailable = session is None and (idx.turn_count or 0) > 0
    if transcript_unavailable:
        log.warning(
            "session_messages: index has turn_count=%s but no ADK session for %s (owner=%s) — transcript unrecoverable",
            idx.turn_count,
            session_id,
            idx.owner_uid,
        )

    from db.chat_sessions import list_turn_authors

    messages = _events_to_messages(events, _author_agent_map(idx, ctx))
    _join_turn_authors(messages, await asyncio.to_thread(list_turn_authors, session_id))
    email = (getattr(ctx, "email", "") or "").strip().lower()
    collaborators = list(idx.collaborators or [])
    show_collaborators = idx.owner_uid == ctx.uid or (bool(email) and email in collaborators)
    return GetSessionMessagesResponse(
        messages=messages,
        session_id=session_id,
        collaborators=collaborators if show_collaborators else [],
        a2ui_surfaces=_state_to_a2ui_surfaces(state),
        transcript_unavailable=transcript_unavailable,
    )


@router.get("/sessions/{session_id}/activity", response_model=GetSessionActivityResponse)
async def get_session_activity(
    session_id: str,
    request: Request,
    view: str | None = None,
    user: User = Depends(get_current_user),  # noqa: B008
) -> GetSessionActivityResponse:
    """Return a session's tool-call history, reconstructed from ADK events.

    Powers the Activity panel after a reload: the live AG-UI stream state is
    in-memory and gone on refresh, but the ADK session still holds every
    turn's function_call / function_response events. Same access policy as
    ``/messages`` — the caller must ``ctx.can_access(idx)``; events are read
    under the OWNER's user_id.

    ``view=digest`` (v6.11.0) returns only the curated tiers — tool calls whose
    ``notability`` is not ``internal`` — for the Home digest and the
    ``aitana session digest`` CLI. Delegations are always notable, so they stay.
    """
    idx = _require_session(session_id)
    ctx = request.state.access
    if not ctx.can_access(idx):
        raise HTTPException(status_code=403, detail="Access denied")

    session_service = get_messages_session_service()
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=idx.owner_uid,
        session_id=session_id,
    )
    events = session.events if session is not None else []
    tool_calls = _events_to_tool_activity(events)
    if view == "digest":
        tool_calls = [t for t in tool_calls if t.notability != "internal"]
    return GetSessionActivityResponse(
        tool_calls=tool_calls,
        delegations=_events_to_delegations(events),
        session_start_ts=_earliest_event_ts(events),
        session_id=session_id,
    )


@router.get("/sessions/{session_id}/state")
async def get_session_state(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Return the raw ADK session state for SESSION_ID.

    Owner-only — session state can include sensitive fields (loaded
    document IDs, iframe-pushed model context, internal app:* keys),
    so this is NOT shared via the same can_access policy as
    /messages. The CLI's ``aiplatform sessions inspect`` uses this
    endpoint to debug iframe→agent context flow (sprint 1.25).

    Returns the state dict verbatim. Empty dict if the ADK session
    hasn't been created yet (which can happen for a freshly-indexed
    session that hasn't received its first message).
    """
    idx = _require_session(session_id)
    ctx = request.state.access
    if not ctx.is_owner(idx):
        raise HTTPException(
            status_code=403,
            detail="Only the session owner can inspect session state",
        )

    session_service = get_session_service()
    # app_name must be APP_NAME, not skill_id — same fix as iframe_context_routes.py
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=idx.owner_uid,
        session_id=session_id,
    )
    if session is None:
        return {}
    return dict(session.state) if session.state else {}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Soft-delete a session (sets archivedAt). Owner-only."""
    idx = _require_session(session_id)
    ctx = request.state.access

    if not ctx.can_access(idx):
        raise HTTPException(status_code=403, detail="Access denied")
    if not ctx.is_owner(idx):
        raise HTTPException(status_code=403, detail="Only the session owner can delete it")

    soft_delete_session(session_id)
