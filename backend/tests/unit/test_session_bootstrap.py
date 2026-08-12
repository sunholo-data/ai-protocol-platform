"""Unit tests for protocols.session_bootstrap_routes.

Tests the bootstrap endpoint logic using mocked Firestore and ADK session
service. Covers: idempotency, owner mismatch 403, Firestore index creation,
and graceful ADK failure fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from auth import User
from db.models.chat_session import ChatSessionIndex
from protocols.session_bootstrap_routes import BootstrapRequest, bootstrap_session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user(uid: str = "user-123", email: str = "user@example.com") -> User:
    u = MagicMock(spec=User)
    u.uid = uid
    # A real User always carries an email; the bootstrap route derives
    # ownerDomain from it (v6.16.0 M4). Setting it keeps the stub honest.
    u.email = email
    return u


def _make_request_stub() -> MagicMock:
    req = MagicMock()
    req.state.access = MagicMock()
    return req


def _make_index(owner_uid: str = "user-123", session_id: str = "sess-abc") -> ChatSessionIndex:
    from datetime import UTC, datetime

    from db.models.access import AccessControl

    return ChatSessionIndex(
        sessionId=session_id,
        skillId="skill-1",
        ownerUid=owner_uid,
        accessControl=AccessControl(type="private"),
        firstMessageAt=datetime.now(UTC),
        lastMessageAt=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_creates_index_when_absent():
    """Fresh session: index created, ADK session created, created=True."""
    user = _make_user("user-123")
    req = _make_request_stub()
    body = BootstrapRequest(skill_id="skill-1")

    mock_service = AsyncMock()
    mock_service.create_session = AsyncMock(return_value=MagicMock())

    with (
        patch("protocols.session_bootstrap_routes.get_session_index", return_value=None) as mock_get,
        patch("protocols.session_bootstrap_routes.create_session_index") as mock_create,
        patch("protocols.session_bootstrap_routes.get_session_service", return_value=mock_service),
    ):
        result = await bootstrap_session("sess-new", body, req, user)

    assert result.created is True
    assert result.session_id == "sess-new"
    mock_get.assert_called_once_with("sess-new")
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["session_id"] == "sess-new"
    assert call_kwargs["skill_id"] == "skill-1"
    assert call_kwargs["owner_uid"] == "user-123"
    mock_service.create_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_idempotent_when_index_exists():
    """Existing session owned by same user: no-op, created=False."""
    user = _make_user("user-123")
    req = _make_request_stub()
    body = BootstrapRequest(skill_id="skill-1")
    existing = _make_index(owner_uid="user-123", session_id="sess-abc")

    with (
        patch("protocols.session_bootstrap_routes.get_session_index", return_value=existing),
        patch("protocols.session_bootstrap_routes.create_session_index") as mock_create,
        patch("protocols.session_bootstrap_routes.get_session_service") as mock_svc,
    ):
        result = await bootstrap_session("sess-abc", body, req, user)

    assert result.created is False
    assert result.session_id == "sess-abc"
    mock_create.assert_not_called()
    mock_svc.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_owner_mismatch_raises_403():
    """Existing session owned by different user → 403."""
    user = _make_user("user-other")
    req = _make_request_stub()
    body = BootstrapRequest(skill_id="skill-1")
    existing = _make_index(owner_uid="user-123", session_id="sess-abc")

    with (
        patch("protocols.session_bootstrap_routes.get_session_index", return_value=existing),
    ):
        with pytest.raises(HTTPException) as exc:
            await bootstrap_session("sess-abc", body, req, user)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_continues_when_adk_create_fails():
    """ADK create_session failure is swallowed — Firestore index still created."""
    user = _make_user("user-123")
    req = _make_request_stub()
    body = BootstrapRequest(skill_id="skill-1")

    mock_service = AsyncMock()
    mock_service.create_session = AsyncMock(side_effect=RuntimeError("ADK unavailable"))

    with (
        patch("protocols.session_bootstrap_routes.get_session_index", return_value=None),
        patch("protocols.session_bootstrap_routes.create_session_index") as mock_create,
        patch("protocols.session_bootstrap_routes.get_session_service", return_value=mock_service),
    ):
        result = await bootstrap_session("sess-new", body, req, user)

    assert result.created is True
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_bootstrap_passes_document_ids():
    """document_ids in request body are forwarded to create_session_index."""
    user = _make_user("user-123")
    req = _make_request_stub()
    body = BootstrapRequest(skill_id="skill-1", document_ids=["doc-a", "doc-b"])

    mock_service = AsyncMock()
    mock_service.create_session = AsyncMock(return_value=MagicMock())

    with (
        patch("protocols.session_bootstrap_routes.get_session_index", return_value=None),
        patch("protocols.session_bootstrap_routes.create_session_index") as mock_create,
        patch("protocols.session_bootstrap_routes.get_session_service", return_value=mock_service),
    ):
        await bootstrap_session("sess-new", body, req, user)

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["document_ids"] == ["doc-a", "doc-b"]
