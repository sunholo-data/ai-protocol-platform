"""ChatSessionIndex — lightweight Firestore index row for chat sessions.

Events and state live in ADK VertexAiSessionService (Agent Engine).
This model is the queryable metadata mirror: list, filter, share, and
rename without touching Agent Engine's O(n) list_sessions scan.

Access enforcement reuses the shared `AccessControl` + `can_access()`
pipeline from resource-access-control (1A.1b). Default at session start:
inherit the parent document's accessControl (copy verbatim).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from db.models.access import AccessControl


class ChatSessionIndex(BaseModel):
    """Firestore document at `chat_sessions/{sessionId}`.

    `owner_id` property satisfies the `_HasAccess` protocol used by
    `auth.access_context.can_access()`.

    ``document_ids`` is the full list of documents that have ever been
    attached to this session — added by ``make_document_loader`` via
    ``ArrayUnion`` whenever the user opens a new tab. The
    ``list_sessions_for_document`` query uses ``array_contains`` so a
    session shows up under each of its docs' history panels.
    """

    session_id: str = Field(alias="sessionId")
    # PROVISIONAL (issue #38 follow-up): the row was pre-created by
    # POST /sessions/{id}/bootstrap when the chat page mounted, BEFORE any
    # message was sent. The row has to exist that early — iframe context pushes
    # (`ui/update-model-context`) 404 without it — but a user who opens a chat
    # and types nothing should not accumulate history. Cleared on the first real
    # turn (`skill_processor._ensure_session_index`), and provisional rows are
    # hidden from the session lists.
    #
    # Absent on rows written before this field existed; absent means NOT
    # provisional, so the list filter must treat missing as visible (a Firestore
    # `where(provisional == False)` would silently drop every legacy row).
    provisional: bool = False
    document_ids: list[str] = Field(default_factory=list, alias="documentIds")
    skill_id: str = Field(alias="skillId")
    # v6.10.0: the chain of skills this thread has run on (confirm→switch appends
    # the target). `skill_id` above is always the most-recent turn's skill.
    skill_history: list[str] = Field(default_factory=list, alias="skillHistory")
    owner_uid: str = Field(alias="ownerUid")
    # v6.16.0 (ADMIN-SCOPE M4): the owner's email domain, denormalised onto the
    # row so tenant-scoped admin analytics can be a real Firestore `where()`
    # instead of an O(rows) uid→domain Auth lookup per request.
    #
    # Blank on rows written before the backfill. Analytics treats blank as
    # OUT OF SCOPE for a tenant admin (fail closed) — an unattributable session
    # must never be shown to the wrong tenant just because its domain is
    # unknown. Platform admins still see everything.
    owner_domain: str = Field(default="", alias="ownerDomain")
    access_control: AccessControl = Field(alias="accessControl")
    title: str | None = None
    turn_count: int = Field(default=0, alias="turnCount")
    first_message_at: datetime = Field(alias="firstMessageAt")
    last_message_at: datetime = Field(alias="lastMessageAt")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    # v6.23.0 B5 Phase 2 — this session's canonical transcript is gone.
    #
    # Set by `aiplatform sessions reconcile --mark-lost` for rows that have turns
    # but no ADK session. Measured on test 2026-08-10: 14 of the 100 most recent
    # sessions, 6 of them ONE's, all casualties of the SessionManager sweep fixed
    # by 44ca9b6 on 2026-08-05 (zero since). The per-session views already say
    # "transcript unavailable" once opened; without this flag the LIST still shows
    # them as ordinary conversations, so a user only discovers the session is
    # empty by clicking it. Unrecoverable, so the honest move is to label them.
    transcript_lost: bool = Field(default=False, alias="transcriptLost")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def owner_id(self) -> str:
        """Satisfies the `_HasAccess` protocol (owner_id field)."""
        return self.owner_uid


__all__ = ["ChatSessionIndex"]
