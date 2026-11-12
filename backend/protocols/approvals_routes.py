"""``/api/approvals`` — list, read and decide human-approval requests over REST.

The approval link page and the CLI decide here; the MCP adapter and the web
card call :func:`adk.approvals.decide` in-process. Every path runs the same
policy (``auth.approvals``). Design: docs/design/v6.41.0/human-approvals.md.

Two kinds of caller:

* a human *session* (the normal ``get_current_user`` verification), deciding
  through the ``link`` channel — the approval page;
* a personal token with the ``approve`` scope (``auth.mcp_tokens``), deciding
  through the ``cli`` channel. A token without that scope is refused here, and
  an approve token still opens nothing else: ``get_current_user`` never
  accepts MCP tokens.

Callers only ever see their own requests: someone else's id answers 404.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from adk import approvals
from auth import User, enforce_user_gates, firebase_auth, get_current_user, mcp_tokens
from auth.approvals import APPROVE_SCOPE, Channel, Credential

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@dataclass(frozen=True)
class Caller:
    user: User
    credential: Credential
    channel: Channel


async def _caller(request: Request) -> Caller:
    header = request.headers.get("Authorization", "")
    token = header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""
    if not mcp_tokens.is_mcp_token(token):
        user = await get_current_user(request)
        return Caller(user, Credential(kind="session", id=user.auth_mode), "link")
    record = await asyncio.to_thread(mcp_tokens.resolve_token_record, token)
    if record is None:
        raise HTTPException(status_code=401, detail="This token is unknown, revoked or expired.")
    if APPROVE_SCOPE not in record["scopes"]:
        raise HTTPException(status_code=403, detail="This token has no approval rights.")
    user = await asyncio.to_thread(firebase_auth.resolve_user_by_uid, record["uid"])
    if user is None:
        raise HTTPException(status_code=401, detail="This token's owner is no longer active.")
    enforce_user_gates(user)
    credential = Credential(kind="token", id=record["id"], scopes=frozenset(record["scopes"]), label=record["name"])
    return Caller(user, credential, "cli")


_Caller = Annotated[Caller, Depends(_caller)]


class DecisionBody(BaseModel):
    decision: Literal["approve", "cancel"]
    digest: str = Field(min_length=1, max_length=128)


def _http(exc: approvals.ApprovalError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("")
async def list_approvals(caller: _Caller, session: str | None = Query(default=None)) -> dict[str, Any]:
    pending = await asyncio.to_thread(approvals.pending_for, caller.user.uid, session)
    return {"approvals": [request.view() for request in pending]}


@router.get("/{approval_id}")
async def read_approval(approval_id: str, caller: _Caller) -> dict[str, Any]:
    try:
        request = await asyncio.to_thread(approvals.get, approval_id, caller.user.uid)
    except approvals.ApprovalError as exc:
        raise _http(exc) from exc
    return request.view()


@router.post("/{approval_id}/decision")
async def decide_approval(approval_id: str, body: DecisionBody, caller: _Caller) -> dict[str, Any]:
    try:
        receipt = await approvals.decide(
            approval_id,
            digest=body.digest,
            decision=body.decision,
            user=caller.user,
            channel=caller.channel,
            credential=caller.credential,
        )
    except approvals.ApprovalError as exc:
        raise _http(exc) from exc
    return {"state": receipt.state, "message": receipt.message, "details": receipt.details}
