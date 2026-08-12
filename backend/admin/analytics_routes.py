"""GET /api/admin/analytics/* — session & trace analytics for admins (9.5 Phase 1).

Read-only, `aitana-admin`-gated. Lists chat sessions (the `chat_sessions` mirror)
and reconstructs a session's full trace (messages, tool calls, delegations) by
reusing the `sessions_route` reconstruction — no new capture. Access-scoped:
Tenant-scoped by ownerDomain (v6.16.0 M4); the transcript endpoint stays
platform-only pending the chat-transcript privacy ruling.

See docs/design/v6.9.0/analytics-and-reporting.md.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from adk.agui import APP_NAME
from admin.scope import AdminScope, Scope
from db.chat_sessions import get_session_index, update_session_fields
from db.firestore import get_document, query_documents
from protocols.sessions_route import (
    _author_agent_map,
    _earliest_event_ts,
    _events_to_delegations,
    _events_to_messages,
    _events_to_tool_activity,
    get_messages_session_service,
    resolve_skill_labels,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/analytics", tags=["admin-analytics"])

_SESSIONS_COLLECTION = "chat_sessions"

# uid → (email, display_name) directory cache. Firebase user records change
# rarely (email/name are ~stable), so a process-lifetime cache is safe and
# spares the admin page a get_users round-trip per uid on every list. Populated
# only on a cache miss; never negatively cached (an unknown uid re-resolves next
# time in case the account was just created). Bounded implicitly by the number
# of distinct users that have ever chatted on the env — fine for an admin tool.
_DIRECTORY_CACHE: dict[str, tuple[str, str]] = {}


def _fb_auth():  # type: ignore[no-untyped-def]
    """firebase_admin.auth, initialising the default app if needed. Returns None
    when Firebase is unavailable (e.g. unit tests without credentials) so every
    caller degrades to the raw uid rather than 500ing."""
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
    except ImportError:  # pragma: no cover - deployed only
        return None
    try:
        firebase_admin.get_app()
    except ValueError:
        try:
            firebase_admin.initialize_app()
        except Exception:  # pragma: no cover - no ADC in tests
            return None
    return fb_auth


def _resolve_user_directory(uids: set[str]) -> dict[str, tuple[str, str]]:
    """Batch-resolve Firebase uids → (email, display_name).

    Best-effort and fail-soft: an unknown uid, a Firebase outage, or a missing
    SDK yields no entry, and the caller falls back to showing the raw uid. Reads
    the process cache first and only calls ``get_users`` for the misses, batched
    at the SDK's 100-identifier limit.
    """
    wanted = {u for u in uids if u}
    resolved: dict[str, tuple[str, str]] = {u: _DIRECTORY_CACHE[u] for u in wanted if u in _DIRECTORY_CACHE}
    misses = [u for u in wanted if u not in resolved]
    if not misses:
        return resolved
    fb = _fb_auth()
    if fb is None:
        return resolved
    try:
        from firebase_admin.auth import UidIdentifier
    except Exception:  # pragma: no cover - deployed only
        return resolved
    for start in range(0, len(misses), 100):
        batch = misses[start : start + 100]
        try:
            result = fb.get_users([UidIdentifier(u) for u in batch])
        except Exception as exc:  # pragma: no cover - deployed only
            log.warning("analytics: user directory lookup failed for %d uids: %s", len(batch), exc)
            continue
        for rec in getattr(result, "users", []) or []:
            entry = (getattr(rec, "email", "") or "", getattr(rec, "display_name", "") or "")
            _DIRECTORY_CACHE[rec.uid] = entry
            resolved[rec.uid] = entry
    return resolved


def _ts(v: Any) -> str:
    """Firestore timestamp / datetime → ISO string (empty if absent)."""
    if v is None:
        return ""
    iso = getattr(v, "isoformat", None)
    return iso() if callable(iso) else str(v)


class SessionRow(BaseModel):
    session_id: str
    skill_id: str = ""
    # Friendly skill name (CLAUDE.md #9) — admins pick sessions by "ONE PPA
    # Expert", never by UUID. Empty when the skill can't be resolved.
    skill_label: str = ""
    owner_uid: str = ""
    owner_domain: str = ""
    # Resolved from Firebase Auth (best-effort) so admins read a name/email, not
    # an opaque uid. Empty when the directory lookup misses; the UI falls back to
    # the uid. (Friendly-names principle — CLAUDE.md #9.)
    owner_email: str = ""
    owner_name: str = ""
    title: str = ""
    turn_count: int = 0
    document_count: int = 0
    first_message_at: str = ""
    last_message_at: str = ""
    archived: bool = False
    # v6.23.0 B5 — the canonical transcript is gone; the list should say so
    # BEFORE an admin clicks into an unrecoverable trace.
    transcript_lost: bool = False


class OwnerFacet(BaseModel):
    """One distinct session owner in scope, for the user selector."""

    uid: str
    email: str = ""
    name: str = ""
    sessions: int = 0
    last_active: str = ""


class SkillFacet(BaseModel):
    """One distinct skill in scope, for the skill selector."""

    id: str
    label: str = ""
    sessions: int = 0


class SessionListResponse(BaseModel):
    sessions: list[SessionRow]
    # Facets are computed over ALL in-scope rows (before the q/skill/owner
    # filters and the limit truncation) so the selectors always show the full
    # population with stable counts, whatever is currently filtered.
    owners: list[OwnerFacet] = []
    skills: list[SkillFacet] = []
    # Never-used husks excluded from the list (bootstrap rows nothing was ever
    # sent to). Reported so the UI can say the list shrank rather than hiding
    # rows silently (CLAUDE.md #8). Measured on dev 2026-08-10: 214 of 483 rows.
    hidden_empty: int = 0


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    scope: Scope,
    skill_id: str | None = Query(None, description="Filter to one skill id."),
    owner_uid: str | None = Query(None, description="Filter to one owner uid."),
    q: str | None = Query(None, description="Substring match on title / owner / skill."),
    limit: int = Query(100, le=500),
) -> SessionListResponse:
    """List chat sessions in scope (newest first), optionally filtered.

    Tenant-scoped by ``ownerDomain``. A row with a **blank** ownerDomain (written
    before the v6.16.0 backfill, or by the uid-only callback path) is treated as
    OUT of a tenant's scope: showing an unattributable session to a tenant admin
    because its owner is unknown would be exactly the leak this milestone
    closes. Platform admins still see every row.
    """
    docs = query_documents(_SESSIONS_COLLECTION)
    rows: list[SessionRow] = []
    hidden_empty = 0
    for d in docs:
        row = SessionRow(
            session_id=str(d.get("__id", "")),
            skill_id=str(d.get("skillId", "") or ""),
            owner_uid=str(d.get("ownerUid", "") or ""),
            owner_domain=str(d.get("ownerDomain", "") or ""),
            title=str(d.get("title", "") or ""),
            turn_count=int(d.get("turnCount", 0) or 0),
            document_count=len(d.get("documentIds") or []),
            first_message_at=_ts(d.get("firstMessageAt")),
            last_message_at=_ts(d.get("lastMessageAt")),
            archived=bool(d.get("archivedAt")),
            transcript_lost=bool(d.get("transcriptLost")),
        )
        # Fail closed: scope.may("") is False, so blank-domain rows are hidden
        # from tenant admins and visible only to platform scope.
        if not scope.may(row.owner_domain):
            continue
        # Never-used husks: a chat page was opened but nothing was ever sent.
        # Recent ones carry `provisional: true` (issue #38) and are already
        # hidden from user lists; the pre-flag ancestors (Apr-Jul 2026) are the
        # same pattern minus the flag — zero turns, never titled, no documents.
        # A zero-turn row that WAS titled or has documents stays visible: someone
        # invested in it, so it's a real (if empty) conversation.
        if bool(d.get("provisional")) or (row.turn_count == 0 and not row.title and row.document_count == 0):
            hidden_empty += 1
            continue
        rows.append(row)

    # Enrich ALL in-scope rows (before any filter) with the Firebase directory
    # and friendly skill labels — the q-filter can then match a real name, and
    # the facets below describe the full in-scope population.
    directory = _resolve_user_directory({r.owner_uid for r in rows})
    labels = resolve_skill_labels([r.skill_id for r in rows])
    for r in rows:
        r.owner_email, r.owner_name = directory.get(r.owner_uid, ("", ""))
        r.skill_label = labels.get(r.skill_id, "")

    owners: dict[str, OwnerFacet] = {}
    skills: dict[str, SkillFacet] = {}
    for r in rows:
        if r.owner_uid:
            o = owners.setdefault(r.owner_uid, OwnerFacet(uid=r.owner_uid, email=r.owner_email, name=r.owner_name))
            o.sessions += 1
            o.last_active = max(o.last_active, r.last_message_at or r.first_message_at)
        if r.skill_id:
            s = skills.setdefault(r.skill_id, SkillFacet(id=r.skill_id, label=r.skill_label))
            s.sessions += 1

    if skill_id:
        rows = [r for r in rows if r.skill_id == skill_id]
    if owner_uid:
        rows = [r for r in rows if r.owner_uid == owner_uid]
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in f"{r.title} {r.owner_uid} {r.owner_email} {r.owner_name} {r.skill_id} {r.skill_label}".lower()
        ]
    rows.sort(key=lambda r: r.last_message_at or r.first_message_at, reverse=True)
    log.info("admin.analytics: list sessions by uid=%s count=%d", scope.user.uid, min(len(rows), limit))
    return SessionListResponse(
        sessions=rows[:limit],
        owners=sorted(owners.values(), key=lambda o: o.sessions, reverse=True),
        skills=sorted(skills.values(), key=lambda s: s.sessions, reverse=True),
        hidden_empty=hidden_empty,
    )


class TraceDocument(BaseModel):
    """A document attached to the session — id plus its friendly filename
    (CLAUDE.md #9; the name falls back to the id when unresolvable)."""

    id: str
    name: str


class SessionTrace(BaseModel):
    session_id: str
    skill_id: str = ""
    skill_label: str = ""
    owner_uid: str = ""
    owner_email: str = ""
    owner_name: str = ""
    title: str = ""
    turn_count: int = 0
    first_message_at: str = ""
    last_message_at: str = ""
    documents: list[TraceDocument] = []
    # Earliest canonical event (epoch ms) — the real session start; None when
    # there are no events. Number of raw ADK events behind the reconstruction.
    session_start_ts: float | None = None
    event_count: int = 0
    # Distinguishes "this session genuinely has no messages" from "the canonical
    # transcript could not be read" (e.g. the ADK/Agent-Engine session is missing
    # while the mirror row survived) — so the UI never renders a blank panel with
    # no explanation. See the 2026-07-23 mirror/canonical divergence (issue TBD).
    transcript_available: bool = True
    messages: list[Any] = []
    tools: list[Any] = []
    delegations: list[Any] = []


def _resolve_documents(doc_ids: list[str]) -> list[TraceDocument]:
    """Best-effort doc-id → friendly filename. A missing or unreadable document
    keeps its id as the name rather than dropping the entry (the admin still
    needs to know a doc was attached)."""
    out: list[TraceDocument] = []
    for did in doc_ids:
        name = ""
        try:
            doc = get_document("parsed_documents", did)
            if doc:
                name = str(doc.get("originalFilename", "") or "")
        except Exception as exc:
            log.warning("analytics: document name lookup failed for %s: %s", did, exc)
        out.append(TraceDocument(id=did, name=name or did))
    return out


@router.get("/sessions/{session_id}", response_model=SessionTrace)
async def get_session_trace(session_id: str, scope: Scope) -> SessionTrace:
    """Reconstruct a session's full trace (messages + tool calls + delegations).

    **Tenant-scoped.** A tenant admin may read the full transcript of sessions
    belonging to their own domain; a platform admin may read any.

    This was deliberately platform-only in Phase 1 pending a product ruling
    (CHAT-TRANSCRIPT-ACCESS), because it returns whole conversations and
    disclosing is not reversible. Ruled on 2026-07-21: the tenant is the data
    controller for its own users' conversations, so its admins may read them.

    Two consequences that follow from that ruling and are therefore intended:
      * Tool ``argsJson`` is no longer redacted for tenant admins. Redacting the
        arguments while serving the conversation they came from protected
        nothing, and dead security code reads as a guarantee that isn't running.
      * A tenant admin can see content that surfaced in a user's session even if
        it came from a document that user owned privately. Same-tenant, and
        implied by "full conversations", but worth knowing.

    The tenant boundary itself is unchanged: ``assert_may`` still refuses a
    session belonging to another domain, and a session with a blank
    ``ownerDomain`` (pre-backfill, unattributable) fails closed for tenant scope.
    """
    idx = get_session_index(session_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Session not found")
    owner = getattr(idx, "owner_uid", "") or getattr(idx, "ownerUid", "")
    owner_domain = getattr(idx, "owner_domain", "") or getattr(idx, "ownerDomain", "") or ""
    skill_id = getattr(idx, "skill_id", "") or getattr(idx, "skillId", "")
    # Fail closed on unknown ownership: an unattributable session must not be
    # readable by a tenant admin just because we can't say whose it is.
    scope.assert_may(owner_domain)

    service = get_messages_session_service()
    session = await service.get_session(app_name=APP_NAME, user_id=owner, session_id=session_id)
    # A mirror row can outlive its canonical session (Agent-Engine eviction, a
    # turn that indexed but never persisted events). `session is None` is that
    # divergence — surface it so the UI shows "transcript unavailable", not an
    # unexplained empty panel. `session` present with zero events is a genuine
    # empty session and stays available=True.
    transcript_available = session is not None
    events = session.events if session is not None else []
    if not transcript_available:
        log.warning(
            "admin.analytics: canonical session MISSING for session=%s owner=%s "
            "(mirror row exists, turnCount=%s) — transcript unavailable",
            session_id,
            owner or "(unknown)",
            getattr(idx, "turn_count", None) or getattr(idx, "turnCount", None),
        )

    email, name = _resolve_user_directory({owner}).get(owner, ("", ""))
    log.info(
        "admin.analytics: trace session=%s domain=%s by uid=%s events=%d available=%s",
        session_id,
        owner_domain or "(unknown)",
        scope.user.uid,
        len(events),
        transcript_available,
    )
    tools = _events_to_tool_activity(events)
    # ctx=None: the admin has no chat AccessContext; the map builder fails soft
    # and still attributes the root skill, so assistant messages carry the agent
    # label where resolvable and fall back to plain "assistant" where not.
    author_map = _author_agent_map(idx, None)
    doc_ids = [str(d) for d in (getattr(idx, "document_ids", None) or []) if d]
    return SessionTrace(
        session_id=session_id,
        skill_id=skill_id,
        skill_label=resolve_skill_labels([skill_id]).get(skill_id, ""),
        owner_uid=owner,
        owner_email=email,
        owner_name=name,
        title=str(getattr(idx, "title", "") or ""),
        turn_count=int(getattr(idx, "turn_count", 0) or 0),
        first_message_at=_ts(getattr(idx, "first_message_at", None)),
        last_message_at=_ts(getattr(idx, "last_message_at", None)),
        documents=_resolve_documents(doc_ids),
        session_start_ts=_earliest_event_ts(events),
        event_count=len(events),
        transcript_available=transcript_available,
        messages=_events_to_messages(events, author_map),
        tools=tools,
        delegations=_events_to_delegations(events),
    )


# ---------------------------------------------------------------------------
# Reconcile — v6.23.0 B5/F5/F6 Phase 1 (MEASUREMENT, not a fix)
# ---------------------------------------------------------------------------
#
# "Admin traces not populating correctly for some sessions" (Mark, during the
# 2026-08-06 UAT demo). The root cause is genuinely unknown, and the design doc
# (docs/design/v6.23.0/trace-completeness-and-access.md) is explicit that Phase 2
# must NOT be designed before this measures what is actually missing.
#
# A session's truth is spread over three stores that can each drift:
#
#   1. CANONICAL  — ADK's session events (Vertex Agent Engine). Ground truth.
#   2. MIRROR     — Firestore `chat_sessions/{id}`. Metadata ONLY; it holds no
#                   messages. It is written from three different places
#                   (bootstrap, first turn, the session-tracker callback), one of
#                   which swallows a canonical-create failure, and its turnCount
#                   is debounced — so it legitimately lags and can outlive the
#                   canonical session entirely.
#   3. TRACE      — what the admin endpoint above actually renders, i.e. the
#                   canonical events after `_events_to_*` reconstruction.
#
# Reporting "what is in one and absent from another" needs all three read in the
# same breath, and two of them are backend-only (Firestore + the session
# service). So the reconcile runs HERE and the CLI renders the result — one
# authenticated admin call rather than the CLI stitching three, one of which
# would be the ADK-native route that is now admin-gated anyway (SEC-1).
#
# Findings are CODES, not prose: the point of Phase 1 is to run this across many
# sessions and count which codes dominate. That distribution is what Phase 2 gets
# designed against.


class ReconcileFinding(BaseModel):
    code: str
    severity: str  # "error" | "warn" | "info"
    detail: str


class ReconcileReport(BaseModel):
    session_id: str
    # --- mirror ---
    mirror_present: bool = False
    owner_uid: str = ""
    owner_domain: str = ""
    skill_id: str = ""
    mirror_turn_count: int = 0
    provisional: bool = False
    archived: bool = False
    # --- canonical ---
    canonical_present: bool = False
    event_count: int = 0
    user_event_count: int = 0
    # Raw part-level counts, BEFORE `_events_to_*` reconstruction. The gap
    # between these and the trace counts below is the whole point of the tool.
    raw_function_calls: int = 0
    raw_tool_calls: int = 0  # excludes delegation fn names, like the trace does
    raw_delegation_calls: int = 0
    raw_function_responses: int = 0
    calls_missing_id: int = 0
    responses_missing_id: int = 0
    text_bearing_events: int = 0
    # --- trace (what the admin UI renders) ---
    trace_messages: int = 0
    trace_tools: int = 0
    trace_tools_errored: int = 0
    trace_delegations: int = 0
    # --- verdict ---
    findings: list[ReconcileFinding] = []


def _reconcile_counts(events: list) -> dict:
    """Part-level counts straight off the canonical events.

    Deliberately re-derived here rather than reusing `_events_to_tool_activity`:
    the reconstruction is the thing under measurement, so counting with it would
    hide exactly the losses we are looking for.
    """
    from protocols.sessions_route import _DELEGATION_FN_NAMES

    out = {
        "raw_function_calls": 0,
        "raw_tool_calls": 0,
        "raw_delegation_calls": 0,
        "raw_function_responses": 0,
        "calls_missing_id": 0,
        "responses_missing_id": 0,
        "text_bearing_events": 0,
        "user_event_count": 0,
    }
    for e in events:
        if getattr(e, "author", None) == "user":
            out["user_event_count"] += 1
        content = getattr(e, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            continue
        if any(getattr(p, "text", None) for p in parts):
            out["text_bearing_events"] += 1
        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                out["raw_function_calls"] += 1
                if fc.name in _DELEGATION_FN_NAMES:
                    out["raw_delegation_calls"] += 1
                else:
                    out["raw_tool_calls"] += 1
                if getattr(fc, "id", None) is None:
                    out["calls_missing_id"] += 1
            fr = getattr(p, "function_response", None)
            if fr is not None:
                out["raw_function_responses"] += 1
                if getattr(fr, "id", None) is None:
                    out["responses_missing_id"] += 1
    return out


def _reconcile_findings(r: ReconcileReport) -> list[ReconcileFinding]:
    """Turn the counts into named, countable discrepancies."""
    f: list[ReconcileFinding] = []
    add = lambda c, s, d: f.append(ReconcileFinding(code=c, severity=s, detail=d))  # noqa: E731

    if not r.mirror_present:
        add("MIRROR_MISSING", "error", "No chat_sessions row — the admin list cannot show this session at all.")
        return f

    if not r.canonical_present:
        # The known divergence (resilient_session.py, issue #30): the mirror kept
        # counting turns while Vertex appends failed silently.
        sev = "error" if r.mirror_turn_count > 0 else "info"
        add(
            "CANONICAL_MISSING",
            sev,
            f"Mirror row exists (turnCount={r.mirror_turn_count}) but the ADK session is gone. "
            "Transcript is unrecoverable; the trace renders 'unavailable'.",
        )
        return f

    if r.raw_tool_calls != r.trace_tools:
        add(
            "TOOL_CALLS_DROPPED",
            "error",
            f"{r.raw_tool_calls} tool calls in the canonical events but {r.trace_tools} in the trace "
            f"({r.raw_tool_calls - r.trace_tools} lost in reconstruction).",
        )

    # Both id-less cases are now HANDLED by the name-based FIFO fallback in
    # `_events_to_tool_activity` (v6.23.0 B5 Phase 2). They stay reportable
    # because the fallback is a heuristic — with two concurrent calls to the same
    # tool it can attribute a result to the wrong one — so an operator reading a
    # surprising trace should know it was in play. They are observations, not
    # defects; before the fix they were the reason a successful tool rendered red.
    if r.responses_missing_id:
        add(
            "RESPONSE_ID_MISSING",
            "warn",
            f"{r.responses_missing_id} function_response part(s) have no id and were paired to their call "
            "by function NAME (FIFO). Correct for sequential calls; ambiguous if the same tool ran "
            "concurrently.",
        )

    if r.calls_missing_id:
        add(
            "CALL_ID_MISSING",
            "warn",
            f"{r.calls_missing_id} function_call part(s) have no id and rely on the same name-based pairing fallback.",
        )

    if r.trace_tools_errored:
        add(
            "TOOLS_RENDER_ERRORED",
            "warn",
            f"{r.trace_tools_errored} of {r.trace_tools} tool calls render as failed (no paired response). "
            "Cross-check against RESPONSE_ID_MISSING before believing the tool actually failed.",
        )

    if r.raw_delegation_calls != r.trace_delegations:
        add(
            "DELEGATIONS_DROPPED",
            "warn",
            f"{r.raw_delegation_calls} delegation calls raw vs {r.trace_delegations} in the trace "
            "(a delegation with no resolvable target is skipped).",
        )

    # turnCount is debounced: `_flush_session_index` writes on turn 1 and then
    # only every `_TURN_FLUSH_INTERVAL` turns, so the mirror can legitimately
    # trail the canonical count by up to that interval. The constant is imported
    # rather than copied — a tolerance that drifts from the writer is how a
    # measurement tool starts crying wolf. (Measured 2026-08-10: a ±1 tolerance
    # flagged 58% of 100 dev sessions, which buried the findings that mattered.)
    from adk.callbacks import _TURN_FLUSH_INTERVAL

    if r.mirror_turn_count and abs(r.mirror_turn_count - r.user_event_count) > _TURN_FLUSH_INTERVAL:
        add(
            "TURN_COUNT_DRIFT",
            "warn",
            f"Mirror turnCount={r.mirror_turn_count} vs {r.user_event_count} user events canonically — "
            f"a gap of {abs(r.mirror_turn_count - r.user_event_count)}, beyond the "
            f"{_TURN_FLUSH_INTERVAL}-turn debounce.",
        )

    if not r.owner_domain:
        add(
            "OWNER_DOMAIN_BLANK",
            "warn",
            "Blank ownerDomain — this session is INVISIBLE to tenant admins (scope fails closed) "
            "and shows only to platform admins.",
        )

    if r.provisional and r.mirror_turn_count > 0:
        add("PROVISIONAL_STUCK", "warn", "Row still marked provisional despite having turns.")

    if not f:
        add("OK", "info", "All three stores agree.")
    return f


async def _build_reconcile(session_id: str, scope: AdminScope) -> ReconcileReport:
    idx = get_session_index(session_id)
    r = ReconcileReport(session_id=session_id)
    if idx is None:
        r.findings = _reconcile_findings(r)
        return r

    r.mirror_present = True
    r.owner_uid = getattr(idx, "owner_uid", "") or ""
    r.owner_domain = getattr(idx, "owner_domain", "") or ""
    r.skill_id = getattr(idx, "skill_id", "") or ""
    r.mirror_turn_count = int(getattr(idx, "turn_count", 0) or 0)
    r.provisional = bool(getattr(idx, "provisional", False))
    r.archived = bool(getattr(idx, "archived_at", None))
    # Same tenant boundary as the trace endpoint — a reconcile reports counts,
    # but it still names a session and its owner, so it is scoped identically.
    scope.assert_may(r.owner_domain)

    service = get_messages_session_service()
    session = await service.get_session(app_name=APP_NAME, user_id=r.owner_uid, session_id=session_id)
    r.canonical_present = session is not None
    events = session.events if session is not None else []
    r.event_count = len(events)

    for k, v in _reconcile_counts(events).items():
        setattr(r, k, v)

    tools = _events_to_tool_activity(events)
    r.trace_messages = len(_events_to_messages(events))
    r.trace_tools = len(tools)
    r.trace_tools_errored = sum(1 for t in tools if getattr(t, "status", None) == "error")
    r.trace_delegations = len(_events_to_delegations(events))

    r.findings = _reconcile_findings(r)
    return r


@router.get("/sessions/{session_id}/reconcile", response_model=ReconcileReport)
async def reconcile_session(session_id: str, scope: Scope) -> ReconcileReport:
    """Compare the canonical store, the Firestore mirror and the rendered trace.

    Read-only and side-effect free. Phase 1 of trace-completeness-and-access:
    quantify the gap before designing a fix.
    """
    report = await _build_reconcile(session_id, scope)
    log.info(
        "admin.analytics: reconcile session=%s by uid=%s findings=%s",
        session_id,
        scope.user.uid,
        ",".join(f.code for f in report.findings),
    )
    return report


class ReconcileSweepResponse(BaseModel):
    scanned: int
    reports: list[ReconcileReport]
    # code -> how many sessions carried it. This is the Phase 1 deliverable: the
    # DISTRIBUTION tells you which defect to fix, a single session does not.
    code_counts: dict[str, int]


@router.get("/sessions-reconcile-sweep", response_model=ReconcileSweepResponse)
async def reconcile_sweep(
    scope: Scope,
    limit: int = Query(20, le=100, description="How many recent sessions to reconcile."),
    skill_id: str | None = Query(None),
) -> ReconcileSweepResponse:
    """Reconcile the N most recent in-scope sessions and tally the finding codes.

    Bounded-concurrent, not serial: each session costs one canonical read, and
    run serially a 100-session sweep exceeded the CLI's HTTP timeout — i.e. the
    tool failed at exactly the size Phase 1 needs it for. A semaphore of 8 keeps
    the fan-out polite; this is an operator tool, never a request-path feature.
    """
    import asyncio

    docs = query_documents(_SESSIONS_COLLECTION)
    rows = []
    for d in docs:
        domain = str(d.get("ownerDomain", "") or "")
        if not scope.may(domain):
            continue
        if skill_id and str(d.get("skillId", "") or "") != skill_id:
            continue
        rows.append((_ts(d.get("lastMessageAt")) or _ts(d.get("firstMessageAt")), str(d.get("__id", ""))))
    rows.sort(reverse=True)

    sem = asyncio.Semaphore(8)

    async def _one(sid: str) -> ReconcileReport | None:
        async with sem:
            try:
                return await _build_reconcile(sid, scope)
            except HTTPException:
                return None  # scope refusal mid-sweep — skip, don't abort the run
            except Exception:
                # One unreadable session must not sink a 100-session measurement.
                log.warning("admin.analytics: reconcile failed for session=%s", sid, exc_info=True)
                return None

    ids = [sid for _, sid in rows[:limit] if sid]
    results = await asyncio.gather(*(_one(sid) for sid in ids))

    reports: list[ReconcileReport] = [r for r in results if r is not None]
    counts: dict[str, int] = {}
    for rep in reports:
        for code in {f.code for f in rep.findings}:
            counts[code] = counts.get(code, 0) + 1
    log.info(
        "admin.analytics: reconcile sweep by uid=%s scanned=%d codes=%s",
        scope.user.uid,
        len(reports),
        counts,
    )
    return ReconcileSweepResponse(scanned=len(reports), reports=reports, code_counts=counts)


class MarkLostResponse(BaseModel):
    scanned: int
    marked: list[str]
    already_marked: int


@router.post("/sessions-mark-transcript-lost", response_model=MarkLostResponse)
async def mark_transcript_lost(
    scope: Scope,
    limit: int = Query(100, le=500, description="How many recent sessions to scan."),
    dry_run: bool = Query(True, description="Report what WOULD be marked without writing."),
) -> MarkLostResponse:
    """Flag sessions whose canonical transcript is gone (v6.23.0 B5 Phase 2).

    The per-session views already say "transcript unavailable" once opened. This
    labels the LIST, so a user learns a conversation is empty *before* clicking
    into it. Measured on test 2026-08-10: 14 of the 100 most recent sessions, 6
    of them ONE's — all casualties of the SessionManager sweep fixed on
    2026-08-05, with none since.

    Only ever sets the flag, and only for rows that have turns but no canonical
    session. It never clears it and never deletes a row: the mirror row is the
    only surviving evidence the conversation existed, and a session whose
    canonical read fails *transiently* must not be permanently mislabelled — a
    later run simply re-reports it. Defaults to `dry_run=True` so the scan is
    safe to invoke by accident.
    """
    import asyncio

    docs = query_documents(_SESSIONS_COLLECTION)
    rows = []
    for d in docs:
        if not scope.may(str(d.get("ownerDomain", "") or "")):
            continue
        rows.append((_ts(d.get("lastMessageAt")) or _ts(d.get("firstMessageAt")), str(d.get("__id", ""))))
    rows.sort(reverse=True)

    sem = asyncio.Semaphore(8)

    async def _one(sid: str) -> ReconcileReport | None:
        async with sem:
            try:
                return await _build_reconcile(sid, scope)
            except Exception:
                return None

    ids = [sid for _, sid in rows[:limit] if sid]
    reports = [r for r in await asyncio.gather(*(_one(s) for s in ids)) if r is not None]

    marked: list[str] = []
    already = 0
    for rep in reports:
        lost = rep.mirror_present and not rep.canonical_present and rep.mirror_turn_count > 0
        if not lost:
            continue
        idx = get_session_index(rep.session_id)
        if idx is not None and getattr(idx, "transcript_lost", False):
            already += 1
            continue
        marked.append(rep.session_id)
        if not dry_run:
            update_session_fields(rep.session_id, {"transcriptLost": True})

    log.info(
        "admin.analytics: mark-transcript-lost by uid=%s scanned=%d marked=%d already=%d dry_run=%s",
        scope.user.uid,
        len(reports),
        len(marked),
        already,
        dry_run,
    )
    return MarkLostResponse(scanned=len(reports), marked=marked, already_marked=already)


__all__ = ["router"]
