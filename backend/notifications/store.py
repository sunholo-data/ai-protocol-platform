"""Push subscriptions, one document per user: ``push_subscriptions/{uid}``.

    {email, subscriptions: [{endpoint, keys: {p256dh, auth}, ua, createdAt}]}

Keyed by uid (the caller of /api/push/subscribe); ``email`` is kept alongside
because shared-thread recipients are stored as emails (``collaborators``).
Every function is sync — call through ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from db.firestore import get_document, query_documents, set_document

_COLLECTION = "push_subscriptions"
# A person rarely has more than a phone and a laptop or two; the cap stops a
# browser that re-subscribes on every visit from growing the list forever.
_MAX_PER_USER = 10


def _clean(sub: dict[str, Any]) -> dict[str, Any]:
    keys = sub.get("keys") or {}
    return {"endpoint": str(sub["endpoint"]), "keys": {"p256dh": keys.get("p256dh", ""), "auth": keys.get("auth", "")}}


def add(uid: str, email: str, subscription: dict[str, Any], user_agent: str = "") -> None:
    """Store (or refresh) one browser's subscription for ``uid``."""
    if not subscription.get("endpoint"):
        raise ValueError("subscription has no endpoint")
    doc = get_document(_COLLECTION, uid) or {}
    subs = [s for s in doc.get("subscriptions", []) if s.get("endpoint") != subscription["endpoint"]]
    subs.append({**_clean(subscription), "ua": user_agent[:200], "createdAt": datetime.now(UTC).isoformat()})
    set_document(_COLLECTION, uid, {"email": (email or "").lower(), "subscriptions": subs[-_MAX_PER_USER:]})


def remove(uid: str, endpoint: str) -> None:
    """Forget one subscription (the user turned notifications off there)."""
    doc = get_document(_COLLECTION, uid)
    if not doc:
        return
    subs = [s for s in doc.get("subscriptions", []) if s.get("endpoint") != endpoint]
    set_document(_COLLECTION, uid, {**doc, "subscriptions": subs})


def for_uid(uid: str) -> list[dict[str, Any]]:
    return list((get_document(_COLLECTION, uid) or {}).get("subscriptions", []))


def for_email(email: str) -> list[tuple[str, dict[str, Any]]]:
    """(uid, subscription) pairs for everyone signed in as ``email``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for doc in query_documents(_COLLECTION, filters=[("email", "==", (email or "").lower())]):
        uid = doc.get("__id", "")
        out.extend((uid, s) for s in doc.get("subscriptions", []))
    return out
