"""Append-only admin audit trail (v6.9.0 / 9.1).

Every ``/api/admin`` mutation records who did what, to which target, with the
before/after, in an append-only ``admin_audit`` Firestore collection (one doc
per action, uuid id). Stays inside the GCP project edge (CLAUDE.md security
rule) — no content egress, just access-decision metadata.

Best-effort on write: a failed audit write is logged at ERROR (observable in
Cloud Logging) but never raises into the caller — an audit-store blip must not
fail a legitimate admin action. Losses are therefore OBSERVABLE, not silent.

v6.16.0 Phase 4 adds the READ side (:func:`list_admin_actions`), scoped by
tenant. Until then the trail was write-only, which meant the accountability it
was built to provide was never actually available to the admins it concerns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from db.firestore import query_documents, set_document

logger = logging.getLogger(__name__)

_COLLECTION = "admin_audit"


def record_admin_action(
    *,
    actor_uid: str,
    action: str,
    target: str,
    actor_email: str = "",
    before: Any = None,
    after: Any = None,
) -> None:
    """Append one audit record for an admin mutation.

    Args:
        actor_uid: The admin's Firebase uid (or SA email for service callers).
        action: A stable verb, e.g. ``"grant_group_tag"`` / ``"upsert_client"``.
        target: What was mutated, e.g. an email, domain, or skill id.
        actor_email: The admin's email, when known (nice-to-have for the trail).
        before: State before the mutation (JSON-serialisable), or None.
        after: State after the mutation (JSON-serialisable), or None.

    Best-effort — logs and swallows any write error so the mutation still
    succeeds; the failure is visible in Cloud Logging.
    """
    record = {
        "actorUid": actor_uid,
        "actorEmail": actor_email,
        "action": action,
        "target": target,
        "before": before,
        "after": after,
        "ts": datetime.now(UTC).isoformat(),
    }
    try:
        set_document(_COLLECTION, str(uuid.uuid4()), record)
    except Exception as exc:
        logger.error(
            "admin_audit write FAILED (action=%s target=%s actor=%s): %s",
            action,
            target,
            actor_uid,
            exc,
        )


def list_admin_actions(
    *,
    domains: frozenset[str] | None,
    limit: int = 100,
    action: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Read the audit trail, scoped to ``domains``.

    v6.16.0 Phase 4. This module was **write-only** until now: every admin
    mutation was recorded and none of it was readable in-product, so the
    accountability promise that justified building the trail was never
    discharged for the people it concerns.

    Args:
        domains: ``None`` for platform scope (everything). Otherwise the exact
            domains the caller administers.
        limit: Max rows returned, newest first.
        action: Optional exact-match filter on the action verb.

    Returns:
        ``(rows, scanned)``. ``scanned`` is the true pre-filter count so the
        caller can say "showing N of M" rather than implying the trail is empty
        when it is merely out of scope.

    Scoping derives the tenant from the row's ``target`` via
    :func:`admin.scope.domain_of_key`, because audit rows are keyed by whatever
    the mutation touched — an email, a domain, a doc id, or a global registry id.
    A row whose target belongs to **no** tenant (the wildcard tool-permission
    doc, the group-tag registry, the platform preamble) is a platform-level
    action and is therefore visible only to platform scope: showing a tenant
    admin a change they cannot attribute to their own tenant would leak the
    existence of platform configuration they have no part in.
    """
    from admin.scope import domain_of_key

    try:
        raw = query_documents(_COLLECTION, order_by="ts", order_direction="DESCENDING", limit=None)
    except Exception as exc:  # inspection surface — degrade visibly, never 500
        logger.error("admin_audit read FAILED: %s", exc)
        return [], 0

    scanned = len(raw)
    rows: list[dict[str, Any]] = []
    for r in raw:
        if action and str(r.get("action") or "") != action:
            continue
        if domains is not None:
            target_domain = domain_of_key(str(r.get("target") or ""))
            # Fail closed: a blank target domain is a platform-level action.
            if not target_domain or target_domain not in domains:
                continue
        rows.append(r)
        if len(rows) >= limit:
            break
    return rows, scanned


__all__ = ["list_admin_actions", "record_admin_action"]
