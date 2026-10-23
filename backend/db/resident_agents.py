"""Per-user resident agent records (Firestore).

Firestore path: ``resident_agents/{userHash}``

WHAT THIS IS AND IS NOT

The platform owns the RECORD; the AILANG estate owns the resources. This
collection says which user has an agent, where it is, and what it costs — it
does not create service accounts, buckets or instances, and the platform's
runtime identity has no power to (design P2). A record without resources is a
provisioning request; resources without a record are an orphan, and both states
are visible rather than silently reconciled.

WHY THE KEY IS A HASH

The document id is a one-way hash of the user id, matching the naming the
provisioner derives its service account and bucket from. That keeps an email
out of resource names, IAM policies and another estate's audit log — where it
would be permanent — while still letting either side compute the other's names
from the user alone.

NO CREDENTIAL IS STORED HERE. The OpenRouter key is returned once at creation
and written straight to that user's own secret; this record keeps only its
``keyHash``, which is enough to revoke it, read its usage and change its cap
(design P7).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.firestore import get_client

COLLECTION = "resident_agents"

# States a record can be in. Each is distinguishable by the user who hit it,
# because "you may not have one", "yours is still starting" and "yours broke"
# must never render as the same shrug (design P5).
STATE_REQUESTED = "requested"  # eligible, nothing provisioned yet
STATE_PROVISIONING = "provisioning"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_REVOKED = "revoked"  # tag withdrawn; home retained for the grace period

DEFAULT_MONTHLY_BUDGET_USD = 10.0


def user_hash(user_id: str) -> str:
    """Stable one-way id, identical to the provisioner's `user_hash`.

    Twelve hex characters: long enough that collisions are not a practical
    concern at a ~100-instance-per-project ceiling, short enough for a service
    account id (6-30 chars).
    """
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


class ResidentAgent(BaseModel):
    user_hash: str = Field(alias="userHash")
    # Stored because the KEY is a one-way hash and therefore cannot be filtered
    # by tenant. Without it a tenant admin could only be shown everything or
    # nothing, and "everything" is not an option.
    domain: str | None = None
    state: str = STATE_REQUESTED
    instance: str | None = None
    url: str | None = None
    project: str | None = None
    region: str | None = None
    service_account: str | None = Field(default=None, alias="serviceAccount")
    bucket: str | None = None
    # Provider key handle — NOT the key. Enough to revoke, read usage, re-cap.
    key_hash: str | None = Field(default=None, alias="keyHash")
    budget_usd: float = Field(default=DEFAULT_MONTHLY_BUDGET_USD, alias="budgetUsd")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    last_used_at: datetime | None = Field(default=None, alias="lastUsedAt")
    # Why a failed provision failed, kept so a retry is informed and a user can
    # be told something truer than "try again".
    error: str | None = None

    model_config = ConfigDict(populate_by_name=True)


def get(user_id: str) -> ResidentAgent | None:
    doc = get_client().collection(COLLECTION).document(user_hash(user_id)).get()
    if not getattr(doc, "exists", False):
        return None
    return ResidentAgent(**(doc.to_dict() or {}))


def upsert(user_id: str, **fields: Any) -> ResidentAgent:
    """Create or update a record, without clobbering what it is not setting."""
    h = user_hash(user_id)
    ref = get_client().collection(COLLECTION).document(h)
    existing = ref.get()
    data: dict[str, Any] = (
        (existing.to_dict() or {})
        if getattr(existing, "exists", False)
        else {
            "userHash": h,
            # NOT derived from `user_id`: that is a Firebase UID, never an
            # address (`ask_assistant` reads `invocation.user_id`, which
            # `fast_api_app.stream_skill` sets to `user.uid`). An earlier
            # `"@" in user_id` guess left `domain` permanently None, which
            # withholds every record from every TENANT admin — deny-by-default
            # doing exactly what it should, on a field nothing ever filled.
            # The caller that knows the address passes it (`domain=`).
            "domain": None,
            "state": STATE_REQUESTED,
            "budgetUsd": DEFAULT_MONTHLY_BUDGET_USD,
            "createdAt": datetime.now(UTC),
        }
    )
    data.update({k: v for k, v in fields.items() if v is not None})
    ref.set(data)
    return ResidentAgent(**data)


def list_all() -> list[ResidentAgent]:
    out = []
    for doc in get_client().collection(COLLECTION).stream():
        try:
            out.append(ResidentAgent(**(doc.to_dict() or {})))
        except Exception:  # pragma: no cover - a malformed row must not hide the rest
            continue
    return out


def touch(user_id: str) -> None:
    """Record use. Feeds the idle sweep, so an instance stopped for idleness
    can be explained rather than merely observed."""
    try:
        upsert(user_id, lastUsedAt=datetime.now(UTC))
    except Exception:  # pragma: no cover - telemetry must never fail a turn
        pass
