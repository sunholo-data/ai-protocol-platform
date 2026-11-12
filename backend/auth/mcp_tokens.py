"""Personal MCP access tokens — how a person connects an MCP client to their skills.

An MCP client (Claude Code, Claude Desktop, …) can't hold a Firebase ID token:
it expires hourly and needs a browser to refresh. So a signed-in user mints a
long-lived personal token (``POST /api/mcp/tokens``) and pastes it into their
client config as ``Authorization: Bearer aip_mcp_…``.

Scope: a token opens the MCP endpoint (``/mcp``) ONLY — it is never accepted by
``get_current_user``, so it cannot call the REST API, mint more tokens, or
reach admin routes. Through MCP it acts as its owner: the same skill
visibility, the same tool caller-scoping (``session.user_id`` = owner uid).

Storage: ``mcp_tokens/{sha256(token)}`` — only the hash is kept, so a
Firestore read can't replay a token; the plaintext is shown once at creation.
Every use re-resolves the owner from Firebase (``resolve_user_by_uid``), so a
disabled account, a revoked claim or a changed domain takes effect on the next
call. Firestore rules deny all client access to the collection.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from db.firestore import delete_document, get_document, query_documents, set_document, update_document

logger = logging.getLogger(__name__)

COLLECTION = "mcp_tokens"
TOKEN_PREFIX = "aip_mcp_"
DEFAULT_DAYS = 90
MAX_DAYS = 365
MAX_TOKENS_PER_USER = 10
_TOUCH_INTERVAL = timedelta(minutes=10)
# Scopes. Every token opens /mcp. An "approve" token may also decide routine
# human approvals (auth.approvals) from an MCP client or the CLI, so it is
# minted only on request, lives shorter, and every use is pushed to the owner.
MCP_SCOPE = "mcp"
APPROVE_SCOPE = "approve"
APPROVE_DEFAULT_DAYS = 30
APPROVE_MAX_DAYS = 90


class TokenError(ValueError):
    """A request the caller can fix; ``str(exc)`` is safe to show them."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(token_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": token_id[:12],
        "name": doc.get("name", ""),
        "createdAt": doc.get("created_at"),
        "expiresAt": doc.get("expires_at"),
        "lastUsedAt": doc.get("last_used_at"),
        "scopes": _scopes(doc),
    }


def _scopes(doc: dict[str, Any]) -> list[str]:
    """A token's scopes; tokens minted before scopes existed open /mcp only."""
    return list(doc.get("scopes") or [MCP_SCOPE])


def is_mcp_token(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def create_token(uid: str, name: str, days: int | None = None, *, approve: bool = False) -> tuple[str, dict[str, Any]]:
    """Mint a token for ``uid``, with approval rights only if ``approve``. Returns (plaintext, public record)."""
    name = (name or "").strip()[:60] or "MCP client"
    longest = APPROVE_MAX_DAYS if approve else MAX_DAYS
    days = (APPROVE_DEFAULT_DAYS if approve else DEFAULT_DAYS) if days is None else days
    if not 1 <= days <= longest:
        raise TokenError(f"A token {'with approval rights ' if approve else ''}can last 1 to {longest} days.")
    if len(list_tokens(uid)) >= MAX_TOKENS_PER_USER:
        raise TokenError(f"You already have {MAX_TOKENS_PER_USER} tokens. Revoke one you no longer use first.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    doc = {
        "uid": uid,
        "name": name,
        "created_at": now,
        "expires_at": now + timedelta(days=days),
        "last_used_at": None,
        "scopes": [MCP_SCOPE, APPROVE_SCOPE] if approve else [MCP_SCOPE],
    }
    token_id = _hash(token)
    set_document(COLLECTION, token_id, doc)
    logger.info("mcp_tokens: created token %s for uid=%s (%d days)", token_id[:12], uid, days)
    return token, _public(token_id, doc)


def list_tokens(uid: str) -> list[dict[str, Any]]:
    rows = query_documents(COLLECTION, filters=[("uid", "==", uid)], limit=50)
    return [_public(r["__id"], r) for r in rows if r.get("__id")]


def revoke_token(uid: str, token_id: str) -> bool:
    """Delete one of ``uid``'s tokens by its public id. False if none matched."""
    token_id = (token_id or "").strip().lower()
    if len(token_id) < 12:
        return False
    for row in query_documents(COLLECTION, filters=[("uid", "==", uid)], limit=50):
        if row.get("__id", "").startswith(token_id):
            delete_document(COLLECTION, row["__id"])
            logger.info("mcp_tokens: revoked token %s for uid=%s", token_id[:12], uid)
            return True
    return False


def resolve_token(token: str) -> str | None:
    """The owner uid of a live token, or None (unknown, malformed or expired)."""
    record = resolve_token_record(token)
    return record["uid"] if record else None


def resolve_token_record(token: str) -> dict[str, Any] | None:
    """``{uid, id, name, scopes}`` of a live token, or None (unknown, malformed or expired)."""
    if not is_mcp_token(token):
        return None
    token_id = _hash(token)
    doc = get_document(COLLECTION, token_id)
    if not doc or not doc.get("uid"):
        return None
    now = datetime.now(UTC)
    expires = doc.get("expires_at")
    if not isinstance(expires, datetime) or expires <= now:
        return None
    last = doc.get("last_used_at")
    if not isinstance(last, datetime) or now - last > _TOUCH_INTERVAL:
        try:
            update_document(COLLECTION, token_id, {"last_used_at": now})
        except Exception:
            logger.debug("mcp_tokens: last_used_at touch failed", exc_info=True)
    return {"uid": str(doc["uid"]), "id": token_id[:12], "name": doc.get("name", ""), "scopes": _scopes(doc)}
