"""Send AILANG messages: a tool for skills, and the platform's own notifier.

AILANG's messaging plane files a message into a named inbox; what happens next
depends on the inbox (an agent picks it up, a human triages it, or nothing).
The plane's coordinator accepts ``POST /api/messages`` with a bearer key, which
is the only contract used here — no direct Firestore/Pub/Sub writes, so the
plane can change its storage without breaking the platform.

Two callers:

* ``send_ailang_message`` — a FunctionTool a skill can be given (tool name
  ``ailang_messages``). It may only send to inboxes an admin has allowed in
  ``admin_config/ailang_messages``; anything else is refused with a message the
  model can relay. Every message says which platform user it was sent for.
* ``notify`` — platform code (e.g. the shared-thread ping in
  ``fast_api_app.stream_skill``). The inbox there comes from operator config,
  not from a model, so the allowlist does not apply.

Config:

* env ``AILANG_COORDINATOR_URL`` — e.g. ``http://127.0.0.1:8765``. Unset means
  messaging is off: the tool says so, ``notify`` logs and returns False.
* env ``AILANG_COORDINATOR_API_KEY`` — the coordinator's ``COORDINATOR_API_KEY``.
* Firestore ``admin_config/ailang_messages`` (survives deploys, editable without
  one)::

      {"enabled": true,
       "from": "sunholo-platform",        # sender name shown in the inbox
       "allowedInboxes": ["daneel", "user"],
       "inboxNotes": {"daneel": "Daneel, the house agent"}}  # optional, shown to the model

  Missing or unreadable means the tool is disabled (fails closed).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_COLLECTION = "admin_config"
_DOCUMENT = "ailang_messages"
_CACHE_TTL_SECONDS = 60.0
_TIMEOUT_SECONDS = 10.0
_DEFAULT_FROM = "ai-platform"
_MAX_TITLE = 120
_MAX_CONTENT = 8000

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def _coordinator() -> tuple[str, str]:
    return (
        os.environ.get("AILANG_COORDINATOR_URL", "").strip().rstrip("/"),
        os.environ.get("AILANG_COORDINATOR_API_KEY", "").strip(),
    )


def is_configured() -> bool:
    """True when a coordinator URL and key are set."""
    url, key = _coordinator()
    return bool(url and key)


def load_settings() -> dict:
    """``admin_config/ailang_messages`` with a TTL cache. ``{}`` on any failure."""
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
            return _cache[1]
    settings: dict = {}
    try:
        from db.firestore import get_document

        settings = get_document(_COLLECTION, _DOCUMENT) or {}
    except Exception as exc:
        log.error("ailang_messages: could not read %s/%s, tool disabled: %s", _COLLECTION, _DOCUMENT, exc)
    with _lock:
        _cache = (now, settings)
    return settings


def clear_cache() -> None:
    """Drop cached settings (tests, and after an admin edit)."""
    global _cache
    with _lock:
        _cache = None


def _sender(settings: dict | None = None) -> str:
    cfg = load_settings() if settings is None else settings
    return str(cfg.get("from") or _DEFAULT_FROM)


def _post(inbox: str, title: str, content: str, sender: str) -> dict[str, Any]:
    """POST one message. Raises RuntimeError with a short, loggable reason."""
    url, key = _coordinator()
    if not (url and key):
        raise RuntimeError("AILANG messaging is not configured on this platform")
    body = {
        "inbox": inbox,
        "title": title[:_MAX_TITLE],
        "content": content[:_MAX_CONTENT],
        "from": sender,
        "category": "notification",
    }
    try:
        resp = httpx.post(
            f"{url}/api/messages",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"AILANG coordinator unreachable: {type(exc).__name__}") from exc
    if resp.status_code >= 300:
        raise RuntimeError(f"AILANG coordinator refused the message (HTTP {resp.status_code})")
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        return {}


async def notify(inbox: str, title: str, content: str) -> bool:
    """Platform-originated message to an operator-configured inbox.

    Never raises: a notification failing must not fail the turn that caused
    it. Returns whether it was sent.
    """
    inbox = (inbox or "").strip()
    if not inbox:
        return False
    try:
        await asyncio.to_thread(_post, inbox, title, content, _sender())
    except Exception as exc:
        log.warning("ailang_messages: notify %s failed: %s", inbox, exc)
        return False
    log.info("ailang_messages: notified inbox=%s title=%r", inbox, title[:60])
    return True


def _caller(tool_context: Any) -> str:
    """Who the message is being sent for — a friendly name where known.

    In a shared thread a collaborator's turn runs as the owner, so the turn's
    author (``turn_author`` in state) wins over the session's user."""
    try:
        author = tool_context.state.get("turn_author")
        if author:
            return str(author)
        uid = str(tool_context._invocation_context.user_id)
    except Exception:
        return "unknown user"
    try:
        from auth.firebase_auth import resolve_user_by_uid

        user = resolve_user_by_uid(uid)
        if user is not None:
            return user.display_name or user.email or uid
    except Exception:
        pass
    return uid


async def send_ailang_message(inbox: str, title: str, message: str, tool_context: Any) -> dict:
    """Send a message to an AILANG agent or team inbox.

    Use this when the user asks you to tell, ask, notify or hand something over
    to an AILANG agent or inbox (for example "message Daneel about this").
    Only inboxes an administrator has allowed can be used; if the inbox is
    refused, tell the user which inboxes are available.

    Args:
        inbox: The inbox name, e.g. "daneel".
        title: A short subject line (under ~80 characters).
        message: The message body. Plain text or markdown. Be specific: the
            recipient has none of this conversation's context.

    Returns:
        ``{"status": "sent", "inbox": ...}`` on success, otherwise
        ``{"status": "error", "error": <reason to tell the user>}``.
    """
    settings = load_settings()
    allowed = [str(i) for i in settings.get("allowedInboxes") or []]
    if not settings or settings.get("enabled") is False:
        return {"status": "error", "error": "Sending AILANG messages is turned off on this platform."}
    if not is_configured():
        return {"status": "error", "error": "AILANG messaging is not configured on this platform."}
    inbox = (inbox or "").strip()
    if inbox not in allowed:
        return {
            "status": "error",
            "error": f"Inbox '{inbox}' is not allowed. Allowed inboxes: {', '.join(allowed) or 'none'}.",
        }
    if not (title or "").strip() or not (message or "").strip():
        return {"status": "error", "error": "A message needs both a title and a body."}
    body = f"{message.strip()}\n\n— sent from the AI platform on behalf of {_caller(tool_context)}"
    try:
        result = await asyncio.to_thread(_post, inbox, title.strip(), body, _sender(settings))
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    out = {"status": "sent", "inbox": inbox}
    msg_id = result.get("message_id") or result.get("id")
    if msg_id:
        out["messageId"] = str(msg_id)
    return out
