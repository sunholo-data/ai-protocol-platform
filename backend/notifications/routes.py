"""/api/push — let a browser subscribe to this user's notifications."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth import User, get_current_user
from notifications import store
from notifications.hooks import payload
from notifications.webpush import push_config, send

router = APIRouter(prefix="/api/push", tags=["push"])


class PushConfig(BaseModel):
    enabled: bool
    publicKey: str | None = None


class SubscribeBody(BaseModel):
    subscription: dict[str, Any]


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.get("/config", response_model=PushConfig)
async def get_config() -> PushConfig:
    enabled, public_key = push_config()
    return PushConfig(enabled=enabled, publicKey=public_key)


@router.post("/subscribe", status_code=204)
async def subscribe(
    body: SubscribeBody,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Response:
    if not push_config()[0]:
        raise HTTPException(status_code=409, detail="Notifications aren't enabled on this deployment.")
    if not body.subscription.get("endpoint"):
        raise HTTPException(status_code=422, detail="Subscription has no endpoint.")
    await asyncio.to_thread(
        store.add, user.uid, user.email or "", body.subscription, request.headers.get("user-agent", "")
    )
    return Response(status_code=204)


@router.delete("/subscribe", status_code=204)
async def unsubscribe(
    body: UnsubscribeBody,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Response:
    await asyncio.to_thread(store.remove, user.uid, body.endpoint)
    return Response(status_code=204)


@router.post("/test", status_code=204)
async def test_push(user: User = Depends(get_current_user)) -> Response:  # noqa: B008
    """Send "Notifications are on" to every device the caller subscribed —
    NEVER SILENT: 409 when off, 404 when nothing is subscribed, 502 when every
    send failed."""
    if not push_config()[0]:
        raise HTTPException(status_code=409, detail="Notifications aren't enabled on this deployment.")
    subs = await asyncio.to_thread(store.for_uid, user.uid)
    if not subs:
        raise HTTPException(status_code=404, detail="No device is subscribed yet — turn notifications on first.")
    message = payload("Notifications are on", "You'll hear when a reply is ready.", "/", "push-test")
    results = [await send(s, message) for s in subs]
    for s, r in zip(subs, results, strict=True):
        if r == "gone":
            await asyncio.to_thread(store.remove, user.uid, s.get("endpoint", ""))
    if "ok" not in results:
        raise HTTPException(
            status_code=502, detail="The push service refused every device. Turn notifications off and on again."
        )
    return Response(status_code=204)
