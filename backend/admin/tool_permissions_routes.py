"""Tool-permission admin plane (v6.9.0 / 9.3).

CRUD over the ``tool_permissions`` Firestore collection (the *second* access
plane — tool *invocation*, enforced at ``adk/callbacks.py`` via
``permissions.can_use_tool``), so it is co-managed via the admin API instead of
a dev seed script only. Doc id is a user email, an email domain, or ``*``
(wildcard); shape ``{type, tools[], denied[]}`` (see ``auth/permissions.py``).

Every write flushes ``permissions.clear_cache()`` — the enforcement path caches
each ``(email, tool)`` decision for 60s, so without the flush a just-changed
rule would stale-allow/deny for up to a minute. Aitana-admin gated + audited.

See docs/design/v6.9.0/user-group-administration.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from admin.audit import record_admin_action
from admin.scope import Scope, domain_of_key
from auth import permissions as perms
from db.firestore import delete_document, get_document, query_documents, set_document

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/tool-permissions", tags=["admin-tool-permissions"])

COLLECTION = perms.COLLECTION  # "tool_permissions"

_VALID_TYPES = {"user", "domain", "wildcard"}


class ToolPermissionDoc(BaseModel):
    """One ``tool_permissions`` document. ``tools`` grants (``["*"]`` = all);
    ``denied`` revokes and wins over ``tools``."""

    type: str
    tools: list[str] = []
    denied: list[str] = []

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
        return v


class ToolPermissionEntry(ToolPermissionDoc):
    """A doc plus its id, for list/GET responses."""

    doc_id: str


def _doc_domain(doc_id: str) -> str:
    """The domain a tool-permission doc id belongs to (email / domain / ``*``).

    Delegates to the shared :func:`admin.scope.domain_of_key` so this and the
    other admin surfaces cannot drift. ``*`` yields ``""`` — the wildcard applies
    to every tenant at once and so belongs to no single one.
    """
    return domain_of_key(doc_id)


def _assert_may_touch(scope: Scope, doc_id: str) -> None:
    """Deny-by-default gate for one tool-permission doc.

    The wildcard doc is platform-only: it grants or denies tools across every
    tenant, so letting a tenant admin edit it would let them change other
    tenants' permissions without ever naming another domain.
    """
    if (doc_id or "").strip() == "*":
        scope.assert_platform()
        return
    scope.assert_may(_doc_domain(doc_id))


@router.get("", response_model=list[ToolPermissionEntry])
def list_tool_permissions(scope: Scope) -> list[ToolPermissionEntry]:
    """List the tool-permission docs **in scope** (filtered, not 403'd)."""
    out: list[ToolPermissionEntry] = []
    for d in query_documents(COLLECTION):
        doc_id = d.pop("__id", "")
        # The wildcard doc is platform-only; tenant admins never see it listed.
        if str(doc_id).strip() == "*":
            if not scope.is_platform:
                continue
        elif not scope.may(_doc_domain(str(doc_id))):
            continue
        try:
            out.append(ToolPermissionEntry(doc_id=doc_id, **d))
        except Exception as exc:  # a malformed legacy doc must not 500 the list
            log.warning("tool-perms: skipping malformed doc %r (%s)", doc_id, type(exc).__name__)
    log.info("admin.tool_permissions: list by uid=%s count=%d", scope.user.uid, len(out))
    return out


@router.get("/{doc_id:path}", response_model=ToolPermissionEntry)
def get_tool_permission(doc_id: str, scope: Scope) -> ToolPermissionEntry:
    """Get one tool-permission doc by id (email / domain / ``*``). 404 if absent."""
    # Scope before existence — a 404 would otherwise confirm which docs exist.
    _assert_may_touch(scope, doc_id)
    data = get_document(COLLECTION, doc_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No tool_permissions doc {doc_id!r}")
    return ToolPermissionEntry(doc_id=doc_id, **data)


@router.put("/{doc_id:path}", response_model=ToolPermissionEntry)
def upsert_tool_permission(doc_id: str, body: ToolPermissionDoc, scope: Scope) -> ToolPermissionEntry:
    """Create or overwrite a tool-permission doc, then flush the perm cache."""
    doc_id = doc_id.strip()
    if not doc_id:
        raise HTTPException(status_code=422, detail="doc id is required")
    _assert_may_touch(scope, doc_id)
    before = get_document(COLLECTION, doc_id)
    data = body.model_dump()
    set_document(COLLECTION, doc_id, data)
    perms.clear_cache()
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="upsert_tool_permission",
        target=doc_id,
        before=before,
        after=data,
    )
    log.info("admin.tool_permissions: upsert id=%s by uid=%s", doc_id, scope.user.uid)
    return ToolPermissionEntry(doc_id=doc_id, **data)


@router.delete("/{doc_id:path}", response_model=ToolPermissionEntry)
def delete_tool_permission(doc_id: str, scope: Scope) -> ToolPermissionEntry:
    """Delete a tool-permission doc, then flush the perm cache. 404 if absent."""
    _assert_may_touch(scope, doc_id)
    before = get_document(COLLECTION, doc_id)
    if before is None:
        raise HTTPException(status_code=404, detail=f"No tool_permissions doc {doc_id!r}")
    delete_document(COLLECTION, doc_id)
    perms.clear_cache()
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="delete_tool_permission",
        target=doc_id,
        before=before,
        after=None,
    )
    log.info("admin.tool_permissions: delete id=%s by uid=%s", doc_id, scope.user.uid)
    return ToolPermissionEntry(doc_id=doc_id, **before)


__all__ = ["router"]
