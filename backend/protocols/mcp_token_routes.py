"""Personal MCP tokens — mint, list, revoke (see ``auth.mcp_tokens``).

  POST   /api/mcp/tokens         {name?, days?} -> {token, ...}  (token shown ONCE)
  GET    /api/mcp/tokens         the caller's tokens, never the secrets
  DELETE /api/mcp/tokens/{id}    revoke one of the caller's tokens

All three need a real signed-in login (``get_current_user``), which never
accepts an MCP token, so a leaked token can't mint more of itself. Only
people may mint: anonymous workshop identities and machine principals are
refused (a machine already has its own token).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import User, get_current_user, mcp_tokens

router = APIRouter(prefix="/api/mcp/tokens", tags=["mcp"])

_PERSON_AUTH_MODES = {"firebase", "tailnet"}


class CreateToken(BaseModel):
    name: str = Field(default="", max_length=60)
    days: int = mcp_tokens.DEFAULT_DAYS


def _person(user: User) -> User:
    if user.auth_mode not in _PERSON_AUTH_MODES or not user.uid:
        raise HTTPException(status_code=403, detail="Only a signed-in person can create MCP tokens.")
    return user


@router.post("")
def create(body: CreateToken, user: User = Depends(get_current_user)) -> dict[str, Any]:  # noqa: B008
    _person(user)
    try:
        token, record = mcp_tokens.create_token(user.uid, body.name, body.days)
    except mcp_tokens.TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**record, "token": token}


@router.get("")
def list_(user: User = Depends(get_current_user)) -> list[dict[str, Any]]:  # noqa: B008
    return mcp_tokens.list_tokens(_person(user).uid)


@router.delete("/{token_id}")
def revoke(token_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:  # noqa: B008
    if not mcp_tokens.revoke_token(_person(user).uid, token_id):
        raise HTTPException(status_code=404, detail="No such token.")
    return {"ok": True}
