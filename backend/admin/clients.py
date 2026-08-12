"""Admin routes for client/tenant management.

Manages `clients/{domain}` Firestore records — the per-client GCS bucket
mapping read by db/clients.py on every document upload. Gated on the shared
`AdminScope` dependency (human-caller JWT, not the SA-allowlist guard used by
the seed endpoint in admin/auth.py).

v6.16.0: scope-aware. A platform admin sees and edits every tenant; a
`tenant-admin:{domain}` holder sees and edits only its own. The list endpoint
FILTERS rather than 403s — a tenant admin listing tenants should get their own,
not an error.

The PUT upsert validates skill references (unknown slug -> 422) via
``admin.tenants.unknown_skill_refs`` and records every mutation to the
append-only ``admin_audit`` trail (v6.9.0 M4). It also invalidates the durable
client-config cache so an edit propagates immediately.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException  # Depends used inside Annotated[]
from pydantic import BaseModel

from admin.audit import record_admin_action
from admin.scope import Scope
from admin.tenants import unknown_skill_refs
from auth import User, get_current_user
from db.clients import ClientConfig, invalidate_client_cache
from db.firestore import delete_document, get_document, query_documents, set_document

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/clients", tags=["admin-clients"])

_COLLECTION = "clients"


# ---------------------------------------------------------------------------
# Non-admin: the caller's own resolved client config (v6.5.0 AUTH-LANDING)
# ---------------------------------------------------------------------------

me_router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientMeResponse(BaseModel):
    """The caller's resolved client config — the subset the frontend needs to
    decide the authenticated landing target. Deliberately omits
    `documents_bucket` (internal) so this non-admin endpoint leaks nothing
    sensitive."""

    domain: str
    display_name: str = ""
    enabled_skills: list[str] | None = None
    default_skill: str | None = None


@me_router.get("/me", response_model=ClientMeResponse)
def get_my_client(user: Annotated[User, Depends(get_current_user)]) -> ClientMeResponse:
    """Resolve the caller's tenant config from their email domain. `default_skill`
    applies the enabled_skills[0] fallback so the frontend gets the effective
    primary skill. Returns empty defaults for unmapped domains."""
    from db.clients import _user_domain, get_client_cached, resolve_default_skill

    domain = _user_domain(user)
    client = get_client_cached(domain) if domain else None
    return ClientMeResponse(
        domain=domain,
        display_name=client.display_name if client else "",
        enabled_skills=client.enabled_skills if client else None,
        default_skill=resolve_default_skill(user),
    )


class ClientConfigUpdate(BaseModel):
    documents_bucket: str | None = None
    display_name: str = ""
    # v6.4.0 ONE-DEMO M1: per-tenant skill visibility filter (additive nullable).
    # None = unchanged for the upsert merge. Non-empty list = filter active.
    # Empty list intentionally collapses to None — "no skills enabled" wouldn't
    # be a useful tenant state; clear via null instead.
    enabled_skills: list[str] | None = None
    # Domain-derived group tags merged into the JWT's groupTags claim at
    # request time (see auth.firebase_auth._apply_derived_group_tags). Same
    # null-vs-empty-list semantics as enabled_skills.
    derived_group_tags: list[str] | None = None
    # v6.5.0 AUTH-LANDING: skill slug a signed-in user lands on with no prior
    # chat. None leaves it unchanged on merge (same as the other fields).
    default_skill: str | None = None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ClientConfig])
def list_clients(scope: Scope) -> list[ClientConfig]:
    """List tenants **in scope**.

    Filtered, not 403'd: a tenant admin asking "what tenants can I administer?"
    should get their own back. Platform admins are unaffected.
    """
    docs = query_documents(_COLLECTION)
    configs = []
    for d in docs:
        domain = d.pop("__id", "")
        d.pop("domain", None)
        if not scope.may(domain):
            continue
        configs.append(ClientConfig(domain=domain, **d))
    log.info(
        "admin.clients: list by uid=%s scope=%s count=%d",
        scope.user.uid,
        "platform" if scope.is_platform else "tenant",
        len(configs),
    )
    return configs


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


@router.get("/{domain}", response_model=ClientConfig)
def get_client(domain: str, scope: Scope) -> ClientConfig:
    # Scope first, then existence: a 404 for an out-of-scope domain would let a
    # tenant admin enumerate which other tenants exist.
    scope.assert_may(domain)
    data = get_document(_COLLECTION, domain)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Client {domain!r} not found")
    data.pop("domain", None)
    return ClientConfig(domain=domain, **data)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


@router.put("/{domain}", response_model=ClientConfig)
def upsert_client(
    domain: str,
    body: ClientConfigUpdate,
    scope: Scope,
) -> ClientConfig:
    scope.assert_may(domain)
    # exclude_unset → a partial PUT only writes the fields the caller actually
    # sent, so `set --default-skill X` can't null out enabled_skills /
    # derived_group_tags / documents_bucket on the merge. Clearing a field is
    # still possible by sending it explicitly as null.
    data = body.model_dump(exclude_unset=True)
    # An empty enabled_skills list is semantically equivalent to None (no
    # filter). The CLI's `--enabled-skills ""` flow already maps "" → None,
    # but defend in depth in case the API is called directly.
    if data.get("enabled_skills") == []:
        data["enabled_skills"] = None
    if data.get("derived_group_tags") == []:
        data["derived_group_tags"] = None

    # v6.9.0 M4: reject unknown skill references (422) BEFORE writing. Degrades
    # to accept when the known-slug set can't be read (guardrail, not access
    # control). Only checks fields the caller actually sent.
    unknown = unknown_skill_refs(
        data.get("enabled_skills") if "enabled_skills" in data else None,
        data.get("default_skill") if "default_skill" in data else None,
        scope.user.uid,
    )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_skill_ref",
                "unknown": unknown,
                "message": f"Unknown skill slug(s): {', '.join(unknown)}",
            },
        )

    before = get_document(_COLLECTION, domain)
    set_document(_COLLECTION, domain, data, merge=True)
    invalidate_client_cache(domain)

    # Re-read and return the MERGED document, not the request body. The write is
    # a correct `merge=True`, but returning `ClientConfig(**data)` rendered every
    # field the caller didn't send as its model default — so a one-field update
    # replied with `derived_group_tags: null, documents_bucket: null, …` and read
    # exactly like it had just wiped a customer's config. (2026-08-05: adding one
    # skill to acme-energy.example's enabled_skills looked destructive; Firestore was
    # fine.) The danger isn't the scare — it's the obvious "repair", re-PUTting
    # every field from a response that never described stored state.
    merged = get_document(_COLLECTION, domain) or data
    merged.pop("domain", None)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="upsert_client",
        target=domain,
        before=before,
        # The merged result, so the audit trail shows what the tenant actually
        # looks like after the change rather than only the delta.
        after=merged,
    )
    log.info(
        "admin.clients: upsert domain=%s by uid=%s enabled_skills_count=%s derived_tags_count=%s",
        domain,
        scope.user.uid,
        len(data.get("enabled_skills") or []),
        len(data.get("derived_group_tags") or []),
    )
    return ClientConfig(domain=domain, **merged)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{domain}", response_model=ClientConfig)
def delete_client(domain: str, scope: Scope) -> ClientConfig:
    scope.assert_may(domain)
    data = get_document(_COLLECTION, domain)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Client {domain!r} not found")
    delete_document(_COLLECTION, domain)
    invalidate_client_cache(domain)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="delete_client",
        target=domain,
        before=data,
        after=None,
    )
    log.info("admin.clients: delete domain=%s by uid=%s", domain, scope.user.uid)
    data.pop("domain", None)
    return ClientConfig(domain=domain, **data)
