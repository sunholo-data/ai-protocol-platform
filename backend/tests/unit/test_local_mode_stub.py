"""Tests for auth/local_mode_stub.py — the LOCAL_MODE auth bypass.

The stub must:
- Accept exactly the well-known stub token
- Reject every other token (incl. empty + real-looking Firebase JWTs)
- Return a deterministic workshop user identity
- Attach an AccessContext to request.state.access
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from auth.local_mode_stub import (
    STUB_TOKEN,
    WORKSHOP_USER_UID,
    build_workshop_user,
    get_current_user_local_mode,
)


class _FakeState:
    def __init__(self):
        self.access = None


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers
        self.state = _FakeState()


@pytest.mark.asyncio
async def test_stub_accepts_correct_token():
    req = _FakeRequest({"Authorization": f"Bearer {STUB_TOKEN}"})
    user = await get_current_user_local_mode(req)
    assert user.uid == WORKSHOP_USER_UID
    assert user.email == "workshop@local"
    assert req.state.access is not None  # AccessContext attached


@pytest.mark.asyncio
async def test_stub_rejects_other_token():
    req = _FakeRequest({"Authorization": "Bearer some-real-looking-jwt.xyz.abc"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_local_mode(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stub_rejects_empty_bearer():
    req = _FakeRequest({"Authorization": "Bearer "})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_local_mode(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stub_rejects_missing_header():
    req = _FakeRequest({})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_local_mode(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_stub_rejects_malformed_header():
    req = _FakeRequest({"Authorization": "Token foo"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_local_mode(req)
    assert exc.value.status_code == 401


def test_build_workshop_user_deterministic():
    a = build_workshop_user()
    b = build_workshop_user()
    assert a.uid == b.uid
    assert a.email == b.email
    assert a.group_tags == b.group_tags


def test_workshop_user_has_workshop_attendee_tag():
    user = build_workshop_user()
    assert "workshop-attendee" in user.group_tags
