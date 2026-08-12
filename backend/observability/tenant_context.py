"""Per-request tenant identity contextvar for OTel span attribution.

Sprint 2.14 (v6.2.0). Pairs with ``tenant_span_processor.py`` which
stamps every started span with the current task's tenant attrs.

Single insertion point: ``set_tenant_context(user)`` is called once
per request at the bottom of ``auth.get_current_user`` (the dispatcher
all 13 ``Depends(get_current_user)`` callers flow through). After that
call, every OTel span emitted during the same async task carries the
tenant attrs — request span, agent span, tool span, LLM-call span.

PII rule (non-negotiable)
-------------------------
Span attributes leak to Cloud Trace and may be subject to GDPR /
CCPA right-of-access queries. The platform's defaults are
deliberately non-PII:

  - ``tenant.uid``         — synthetic id (Firebase uid or anon-group uid)
  - ``tenant.auth_mode``   — enum: "firebase" | "anonymous_group_id" | "local_mode_stub"
  - ``tenant.group_id``    — synthetic short code (only when present)
  - ``tenant.uid_hash``    — SHA256 of email (only when email present; one-way irreversible)

Raw email, display names, and other PII MUST NEVER land on a span.
This module reads ``User`` fields EXPLICITLY (no reflection over
``__dict__``) so a future fork that subclasses ``User`` with a
``display_name`` or similar PII field cannot accidentally leak it
through this code path.

Forks registering enrichers via ``register_tenant_enricher`` are
documented to follow the same rule in
``docs/integrations/tenant-attribution.md``. The platform does NOT
gate enricher outputs — that's fork responsibility. If you're
writing an enricher, hash before returning; never pass through a
raw email, name, or other reversible PII.

Pattern reference
-----------------
Mirrors ``observability.timing._current_tracker`` — the established
contextvar-per-request pattern in this codebase.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from contextvars import ContextVar

from auth.firebase_auth import User

logger = logging.getLogger(__name__)


# ─── Contextvar ──────────────────────────────────────────────────────────────


_tenant_context: ContextVar[dict[str, str] | None] = ContextVar("tenant_context", default=None)


# ─── Enricher registry ───────────────────────────────────────────────────────


TenantEnricher = Callable[[User], dict[str, str]]
"""Forks register enrichers to add per-deployment attrs (e.g.
``tenant.class_id`` resolved from a Firestore lookup of ``user.group_id``).

The returned dict is merged into the platform's defaults via
``dict.update`` so collisions on the same key resolve to the LAST
enricher's value (deterministic last-wins)."""


_registered_enrichers: list[TenantEnricher] = []


def register_tenant_enricher(fn: TenantEnricher) -> None:
    """Register a per-request enricher that returns extra tenant attrs.

    Forks call this once at startup. Calling multiple times appends
    to the registry — enrichers run in registration order during
    ``set_tenant_context``.

    Raises ``TypeError`` on a non-callable — fork misconfiguration
    should fail loud at startup rather than silently no-op. Mirrors
    the same pattern from sprint 2.12's ``register_budget_enforcer``
    and sprint 2.13's ``setArtefactReviewer`` / ``register_artefact_reviewer``.
    """
    if not callable(fn):
        raise TypeError(f"register_tenant_enricher requires a callable; got {type(fn).__name__}")
    _registered_enrichers.append(fn)


def clear_tenant_enrichers() -> None:
    """Drop all registered enrichers. Used by tests; not for production."""
    _registered_enrichers.clear()


# ─── Public API ──────────────────────────────────────────────────────────────


def set_tenant_context(user: User, extra: dict[str, str] | None = None) -> None:
    """Bind the per-request tenant attributes on the current contextvar.

    Called at FastAPI ingress after auth resolves — every span emitted
    in the same async task gets stamped with these attrs by
    ``TenantAttributeSpanProcessor``. Concurrent requests under
    different tenants run in different async tasks so their
    contextvars stay isolated automatically.

    The ``extra`` kwarg lets the caller add per-request attrs without
    going through the enricher registry (e.g. per-skill tagging set
    inside ``skill_processor``).
    """
    attrs: dict[str, str] = {
        "tenant.uid": user.uid,
        "tenant.auth_mode": user.auth_mode,
    }
    if user.group_id:
        attrs["tenant.group_id"] = user.group_id
    if user.email:
        attrs["tenant.uid_hash"] = _hash_email(user.email)

    # Note: ``User`` has no display_name field; if a future fork adds
    # one, this function still doesn't read it. Display names + raw
    # emails NEVER land on spans (PII rule).

    for fn in _registered_enrichers:
        try:
            attrs.update(fn(user))
        except Exception as exc:
            logger.warning(
                "tenant_context: enricher %s raised — skipping. Other enrichers continue. Error: %s",
                getattr(fn, "__name__", repr(fn)),
                exc,
            )

    if extra:
        attrs.update(extra)

    _tenant_context.set(attrs)


def get_tenant_context() -> dict[str, str] | None:
    """Return the current task's tenant attrs, or None if unset.

    ``TenantAttributeSpanProcessor.on_start`` reads this on every
    span start. None → span emits with no tenant attrs (no crash).
    """
    return _tenant_context.get()


# ─── Internal helpers ────────────────────────────────────────────────────────


def _hash_email(email: str) -> str:
    """One-way SHA256 of email → hex digest.

    Cryptographically irreversible; same input always produces the
    same digest so per-cohort traces can correlate without leaking
    the underlying email. Forks needing different identity-stable
    hashing should write their own enricher.
    """
    return hashlib.sha256(email.encode("utf-8")).hexdigest()
