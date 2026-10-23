"""GET/PATCH /api/admin/residents — who has a resident agent, and what it costs.

Three questions only (design P4): who may have one, what state is theirs in,
what is it costing. Not a dashboard for its own sake.

Tenant-scoped through the same ``Scope`` dependency as every other admin route,
so a tenant admin sees and edits their own users and nobody else's. That is not
a per-route check here — it is the shared primitive, for the reason
``admin/scope.py`` explains at length: routes that each invent their own scoping
are how nineteen slightly different rules happen.

WHAT THIS DOES NOT DO

It does not create service accounts, buckets or instances. The platform records
the decision; the AILANG estate performs it (design P2), because handing a
customer-facing web backend the power to mint identities in the estate that
holds the agent fleet is a far larger blast radius than the feature.

THE PATH TAKES AN EMAIL; THE RECORD IS KEYED BY UID

An admin knows a colleague's address, not their Firebase uid, so the route
speaks the friendly name and resolves it (CLAUDE.md #9: friendly -> id, never
the reverse). The record itself is keyed by ``user_hash(uid)`` because that is
what ``ask_assistant`` looks up -- the ADK invocation carries ``user.uid``
(``fast_api_app.stream_skill``) and the provisioner derives the instance's
service account from the same hash.

Hashing the EMAIL here instead would have made every lookup 404 and every
budget PATCH mint a SECOND, orphan record under a key nothing reads -- so the
spend cap would appear to be set and would cap nothing. Found 2026-09-04 while
enabling per-user mode, before either surface had a real record to disagree
about.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.audit import record_admin_action
from admin.scope import Scope, domain_of_key
from db import resident_agents as records

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/residents", tags=["admin-residents"])

# A cap is a spend control, so it is bounded on BOTH sides. Zero would read as
# "unlimited" to at least one future caller, and an unbounded upper end turns a
# typo into a five-figure month.
MIN_BUDGET_USD = 1.0
MAX_BUDGET_USD = 500.0


class ResidentView(BaseModel):
    user_hash: str = Field(alias="userHash")
    state: str
    url: str | None = None
    instance: str | None = None
    budget_usd: float = Field(alias="budgetUsd")
    spend_usd: float | None = Field(default=None, alias="spendUsd")
    remaining_usd: float | None = Field(default=None, alias="remainingUsd")
    last_used_at: str | None = Field(default=None, alias="lastUsedAt")
    error: str | None = None

    model_config = {"populate_by_name": True}


class BudgetPatch(BaseModel):
    budget_usd: float = Field(alias="budgetUsd")
    model_config = {"populate_by_name": True}


def _fb_auth():
    """firebase_admin.auth, initializing the default app if needed (local twin
    of the same helper in access_routes/users_routes to avoid an import cycle)."""
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
    except ImportError:  # pragma: no cover - deployed only
        return None
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    return fb_auth


def _uid_for(email: str) -> str:
    """The Firebase uid behind an address, or "" if there is none.

    Deliberately NOT falling back to hashing the email: a record under a key
    nothing reads is worse than a clean 404, because it looks like success.
    """
    fb = _fb_auth()
    if fb is None:  # pragma: no cover - deployed only
        return ""
    try:
        return getattr(fb.get_user_by_email(email), "uid", "") or ""
    except Exception:
        return ""


def _record_for(email: str) -> records.ResidentAgent:
    """The record for an address, or a 404 that says WHICH thing is missing."""
    uid = _uid_for(email)
    if not uid:
        raise HTTPException(status_code=404, detail=f"No such user: {email} has never signed in")
    rec = records.get(uid)
    if rec is None:
        # 404 rather than an empty 200: "no resident agent" and "a resident
        # agent with nothing in it" are different facts.
        raise HTTPException(status_code=404, detail=f"No resident agent record for {email}")
    return rec


def _view(rec: records.ResidentAgent) -> ResidentView:
    return ResidentView(
        userHash=rec.user_hash,
        state=rec.state,
        url=rec.url,
        instance=rec.instance,
        budgetUsd=rec.budget_usd,
        lastUsedAt=rec.last_used_at.isoformat() if rec.last_used_at else None,
        error=rec.error,
    )


@router.get("", response_model=list[ResidentView])
def list_residents(scope: Scope) -> list[ResidentView]:
    """Every resident record the caller may see.

    Records are keyed by a one-way hash, so tenant filtering cannot be done on
    the key. A PLATFORM admin sees all; a tenant admin sees only records whose
    stored domain is in scope — and a record with no domain is withheld rather
    than shown, because deny-by-default is the only safe reading of "we do not
    know whose this is".
    """
    out = []
    for rec in records.list_all():
        domain = getattr(rec, "domain", None)
        if not scope.is_platform:
            if not domain or not scope.may(domain):
                continue
        out.append(_view(rec))
    return out


@router.get("/{email}", response_model=ResidentView)
def get_resident(email: str, scope: Scope) -> ResidentView:
    scope.assert_may(domain_of_key(email))
    return _view(_record_for(email))


@router.patch("/{email}/budget", response_model=ResidentView)
def set_budget(email: str, body: BudgetPatch, scope: Scope) -> ResidentView:
    """Change a user's monthly model budget.

    Gated by the same scope as granting the role, because raising a budget is
    the same class of act as issuing one — and audited for the same reason.
    """
    scope.assert_may(domain_of_key(email))
    if not (MIN_BUDGET_USD <= body.budget_usd <= MAX_BUDGET_USD):
        raise HTTPException(
            status_code=422,
            detail=f"Budget must be between ${MIN_BUDGET_USD:g} and ${MAX_BUDGET_USD:g} per month",
        )
    rec = _record_for(email)

    previous = rec.budget_usd
    # By UID, matching the key `_record_for` just read. `upsert` CREATES when
    # absent, so passing the email here would mint an orphan under a key
    # `ask_assistant` never reads -- a cap that caps nothing.
    updated = records.upsert(_uid_for(email), budgetUsd=body.budget_usd)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="set_resident_budget",
        target=email,
        before={"budget_usd": previous},
        after={"budget_usd": body.budget_usd},
    )
    log.info("resident budget for %s: $%s -> $%s", email, previous, body.budget_usd)
    return _view(updated)
