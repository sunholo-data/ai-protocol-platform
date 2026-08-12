"""GET /api/admin/audit — read the admin audit trail (v6.16.0 Phase 4).

The trail has been written since v6.9.0 and readable by nobody: records
accumulated in ``admin_audit`` and were reachable only via raw Firestore. An
audit trail the admin it concerns cannot read does not discharge the
accountability promise that justified building it — especially once client-side
tenant admins are operating their own tenant.

Tenant-scoped: a tenant admin sees actions targeting their own domain; a
platform admin sees everything. Read-only, so no audit row is written for
reading — consistent with ``access_routes`` (the trail is for changes; this is
an inspection).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from admin.audit import list_admin_actions
from admin.scope import Scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/audit", tags=["admin-audit"])


class AuditRow(BaseModel):
    """One recorded admin mutation."""

    id: str = ""
    ts: str = ""
    actor_uid: str = Field(default="", alias="actorUid")
    actor_email: str = Field(default="", alias="actorEmail")
    action: str = ""
    target: str = ""
    before: Any = None
    after: Any = None

    model_config = {"populate_by_name": True}


class AuditResponse(BaseModel):
    entries: list[AuditRow] = Field(default_factory=list)
    # True pre-filter count, so the UI can distinguish "nothing has happened"
    # from "nothing here concerns your tenant" — those look identical otherwise
    # and the second one silently reads as the first.
    scanned: int = 0
    scope: str = "platform"

    model_config = {"populate_by_name": True}


@router.get("", response_model=AuditResponse)
def read_audit(
    scope: Scope,
    limit: int = Query(100, le=500, description="Max entries, newest first."),
    action: str | None = Query(None, description="Exact-match filter on the action verb."),
) -> AuditResponse:
    """Return the audit trail in scope, newest first."""
    rows, scanned = list_admin_actions(domains=scope.domains, limit=limit, action=action)
    entries = [
        AuditRow(
            id=str(r.get("__id", "") or ""),
            ts=str(r.get("ts", "") or ""),
            actorUid=str(r.get("actorUid", "") or ""),
            actorEmail=str(r.get("actorEmail", "") or ""),
            action=str(r.get("action", "") or ""),
            target=str(r.get("target", "") or ""),
            before=r.get("before"),
            after=r.get("after"),
        )
        for r in rows
    ]
    log.info(
        "admin.audit: read by uid=%s scope=%s returned=%d scanned=%d",
        scope.user.uid,
        "platform" if scope.is_platform else "tenant",
        len(entries),
        scanned,
    )
    return AuditResponse(
        entries=entries,
        scanned=scanned,
        scope="platform" if scope.is_platform else "tenant",
    )


__all__ = ["router"]
