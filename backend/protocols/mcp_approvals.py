"""Ask for human approvals over MCP, by elicitation (protocol 2026-07-28).

When a skill run leaves approvals pending (``adk.approvals``), the MCP tool
call does not just end. It returns an ``InputRequiredResult`` asking about
each one; the client asks the person and retries the call with their answers
plus our sealed ``requestState``. The retry NEVER runs the skill again, so the
model never sees, answers or influences the prompt. Clients must speak
2026-07-28: older ones need a back-channel this stateless server doesn't keep.

* **Form** — when the caller's token may approve it here
  (``auth.approvals.can_approve`` on the ``mcp`` channel: an approve-scoped
  token, a routine request, the owner) and the review fits. The retry decides
  it exactly as reviewed.
* **URL** — otherwise (high risk, or a token without approval rights), when the
  client supports URL elicitation and the web app's address is known
  (``APP_BASE_URL``): the person decides on ``/approve/<id>`` after signing in,
  and the retry only reports what happened.
* Otherwise the reply says how to approve it: the CLI or the web app.

Design: docs/design/v6.41.0/human-approvals.md ("MCP: Multi Round-Trip Requests").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from mcp.types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitRequestURLParams,
    InputRequiredResult,
    TextContent,
)

from adk import approvals
from auth.approvals import can_approve

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from adk.approvals import ApprovalRequest
    from auth.approvals import Credential
    from auth.firebase_auth import User

logger = logging.getLogger(__name__)

MODERN_PROTOCOL = "2026-07-28"
MAX_REVIEW_CHARS = 8000
MAX_STATE_TEXT_CHARS = 16000
EMPTY_REPLY = "(The skill finished without a text answer.)"
_FORM_SCHEMA = {
    "type": "object",
    # Accept is the approval: the box starts ticked, and unticking it is the
    # explicit way to submit without approving (live 2026-11-02, a default-off
    # box made "Accept" look like approval while it saved nothing).
    "properties": {
        "approve": {
            "type": "boolean",
            "title": "Approve and save",
            "description": "Untick to leave it waiting without saving.",
            "default": True,
        }
    },
    "required": ["approve"],
}


def _elicitation_modes(context: Context[Any, Any] | None) -> set[str]:
    if context is None or (context.protocol_version or "") < MODERN_PROTOCOL:
        return set()
    elicitation = getattr(context.client_capabilities, "elicitation", None)
    if elicitation is None:
        return set()
    modes = {mode for mode in ("form", "url") if getattr(elicitation, mode, None) is not None}
    # An empty elicitation capability means form mode (spec back-compat).
    return modes or {"form"}


def approval_url(request: ApprovalRequest) -> str | None:
    """The absolute ``/approve/<id>`` link, when the web app's address is configured."""
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/approve/{quote(request.id, safe='')}" if base else None


def _askable(request: ApprovalRequest, user: User, credential: Credential | None) -> bool:
    if credential is None or len(request.review) > MAX_REVIEW_CHARS:
        return False
    return can_approve(user, request, channel="mcp", credential=credential) is None


def where_to_approve(request: ApprovalRequest) -> str:
    """The pending request and how to decide it — with its review, so the reader sees what waits."""
    if request.risk == "high":
        how = "It needs a fresh sign-in: approve it in the web app."
    else:
        how = f"Approve it with `aiplatform approvals approve {request.id}`, or in the web app."
    return f"Waiting for your approval: {request.title}\n\n{request.review}\n\n{how}"


def _form(request: ApprovalRequest) -> ElicitRequest:
    message = (
        f"{request.title}\n\n{request.review}\n\n"
        "Accept to approve and save this, exactly as shown. Decline to discard it."
    )
    return ElicitRequest(params=ElicitRequestFormParams(mode="form", message=message, requested_schema=_FORM_SCHEMA))


def _page(request: ApprovalRequest, url: str) -> ElicitRequest:
    message = f"{request.title}: open the approval page to review it and decide. You'll need to sign in."
    return ElicitRequest(params=ElicitRequestURLParams(mode="url", message=message, url=url, elicitation_id=request.id))


def _text(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


async def after_run(
    text: str,
    session_id: str,
    *,
    user: User | None,
    credential: Credential | None,
    context: Context[Any, Any] | None,
) -> CallToolResult | InputRequiredResult:
    """The tool result for a finished skill run: its text, or a request to approve what it prepared."""
    pending = await asyncio.to_thread(approvals.pending_for, user.uid, session_id) if user else []
    if not pending:
        return _text(text or EMPTY_REPLY)
    text = text or "Prepared for your approval: " + "; ".join(r.title for r in pending) + "."
    modes = _elicitation_modes(context)
    requests: dict[str, ElicitRequest] = {}
    ask: dict[str, dict[str, str]] = {}
    rest: list[str] = []
    for request in pending:
        url = approval_url(request)
        if "form" in modes and _askable(request, user, credential):
            requests[request.id] = _form(request)
            ask[request.id] = {"digest": request.digest, "title": request.title, "mode": "form"}
        elif "url" in modes and url:
            requests[request.id] = _page(request, url)
            ask[request.id] = {"digest": request.digest, "title": request.title, "mode": "url", "url": url}
        else:
            rest.append(where_to_approve(request))
    if not requests:
        return _text("\n\n".join([text, *rest]))
    state = {"text": text[:MAX_STATE_TEXT_CHARS], "ask": ask, "rest": rest}
    return InputRequiredResult(input_requests=requests, request_state=json.dumps(state))


async def resume(context: Context[Any, Any], *, user: User | None, credential: Credential | None) -> CallToolResult:
    """Decide what the person answered. The SDK has already verified the sealed state is ours and theirs."""
    if user is None or credential is None:
        return _text("Sign in with a personal token to decide approvals.")
    state = json.loads(context.request_state or "{}")
    responses = context.input_responses or {}
    lines = [state.get("text", "")]
    for approval_id, asked in (state.get("ask") or {}).items():
        if asked.get("mode") == "url":
            # Accepting a URL elicitation is only consent to open the page; the page decides.
            lines.append(await _page_outcome(approval_id, asked, user))
            continue
        answer = responses.get(approval_id)
        action = getattr(answer, "action", None)
        content = getattr(answer, "content", None) or {}
        if action == "accept" and content.get("approve", True) is True:
            decision = "approve"
        elif action == "decline":
            decision = "cancel"
        else:
            why = (
                "You submitted it with “Approve and save” unticked"
                if action == "accept"
                else "The approval dialog was dismissed"
            )
            lines.append(
                f"Not saved: {asked['title']}. {why}, so nothing was saved. It is still waiting: "
                f"`aiplatform approvals approve {approval_id}`, or the web app."
            )
            continue
        try:
            receipt = await approvals.decide(
                approval_id,
                digest=asked["digest"],
                decision=decision,
                user=user,
                channel="mcp",
                credential=credential,
            )
            lines.append(receipt.message)
        except approvals.ApprovalError as exc:
            lines.append(f"{asked['title']}: {exc}")
    lines.extend(state.get("rest") or [])
    return _text("\n\n".join(line for line in lines if line))


async def _page_outcome(approval_id: str, asked: dict[str, str], user: User) -> str:
    try:
        request = await asyncio.to_thread(approvals.get, approval_id, user.uid)
    except approvals.ApprovalError as exc:
        return f"{asked['title']}: {exc}"
    if request.state == "pending":
        return f"Still waiting for your decision on the approval page: {asked['url']}"
    return f"{asked['title']}: {request.state}."
