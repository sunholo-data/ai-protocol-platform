"""The authenticated artefact route — the only way screenshot bytes leave.

A screenshot of a page the browser reached while logged in is a derived
artefact of confidential content, which is exactly what CLAUDE.md's security
rule opens with. So the interesting assertions here are the negative ones: no
token, another user's token, and a filename that tries to climb out of the
caller's own namespace.

The strongest property is structural rather than checked. The artefact is
stored user-namespaced (`user:<id>`), the route re-adds that prefix itself, and
the uid comes from the verified token — never from the URL. A caller therefore
cannot name an artefact belonging to anyone else, whatever they put in the path.
The tests below pin that, rather than trusting a comparison someone could later
delete.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.genai import types as genai_types

from auth import User, get_current_user
from tools.browser_artefact_routes import router

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_ARTEFACT = "browser_screenshot_e-abc_deadbeef12.png"


def _client(uid: str = "owner") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    return TestClient(app)


def _no_auth_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _part(data: bytes = _PNG, mime: str = "image/png") -> genai_types.Part:
    return genai_types.Part.from_bytes(data=data, mime_type=mime)


# --- the happy path, so the gate is closed and not welded shut ---


def test_owner_gets_the_bytes():
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=_part())
        r = _client().get(f"/api/browser/artefacts/{_ARTEFACT}")

    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG"), "must be the real bytes, not a placeholder"
    assert r.headers["content-type"].startswith("image/png")


def test_the_lookup_uses_the_token_uid_and_the_user_namespace():
    """Neither the uid nor the namespace may come from the URL."""
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=_part())
        _client(uid="owner").get(f"/api/browser/artefacts/{_ARTEFACT}")
        kwargs = svc.return_value.load_artifact.await_args.kwargs

    assert kwargs["user_id"] == "owner"
    assert kwargs["filename"] == f"user:{_ARTEFACT}"
    assert kwargs.get("session_id") is None, "user-namespaced artefacts are not session-scoped"


def test_response_is_never_cached_by_a_shared_cache():
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=_part())
        r = _client().get(f"/api/browser/artefacts/{_ARTEFACT}")

    assert "private" in r.headers.get("cache-control", "")
    assert "no-store" in r.headers.get("cache-control", "")


# --- the negatives, which are the point ---


def test_no_token_is_refused():
    r = _no_auth_client().get(f"/api/browser/artefacts/{_ARTEFACT}")
    assert r.status_code in (401, 403), "an unauthenticated caller must never reach bytes"
    assert not r.content.startswith(b"\x89PNG")


def test_another_users_artefact_is_not_reachable():
    """A non-owner asking for a real artefact id gets nothing.

    404 rather than 403, deliberately: the caller's uid scopes the lookup, so
    from the route's point of view the artefact does not exist. Answering 403
    would confirm that it does — telling a stranger that a given screenshot is
    real, which is a smaller leak than the bytes but a leak all the same.
    """
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        # The other user's namespace holds nothing under this name.
        svc.return_value.load_artifact = AsyncMock(return_value=None)
        r = _client(uid="someone-else").get(f"/api/browser/artefacts/{_ARTEFACT}")

    assert r.status_code == 404
    assert not r.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "bad",
    [
        "user:browser_screenshot_x.png",  # trying to pick the namespace
        "../../etc/passwd",
        "a/b.png",
        "..",
        "",
        "sess-1/user:thing.png",
    ],
)
def test_a_caller_cannot_name_anything_outside_their_own_namespace(bad: str):
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=_part())
        r = _client().get(f"/api/browser/artefacts/{bad}")

    assert r.status_code in (400, 404), f"{bad!r} should not resolve"
    assert not r.content.startswith(b"\x89PNG")


def test_a_missing_artefact_is_a_clean_404_not_a_500():
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=None)
        r = _client().get(f"/api/browser/artefacts/{_ARTEFACT}")
    assert r.status_code == 404


def test_a_storage_failure_is_reported_not_swallowed():
    """Never silent (CLAUDE.md #8) — the caller learns the fetch failed."""
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(side_effect=RuntimeError("bucket down"))
        r = _client().get(f"/api/browser/artefacts/{_ARTEFACT}")
    assert r.status_code == 502
    assert not r.content.startswith(b"\x89PNG")


def test_an_artefact_with_no_bytes_is_not_served_as_empty_success():
    with patch("tools.browser_artefact_routes.get_artifact_service") as svc:
        svc.return_value.load_artifact = AsyncMock(return_value=genai_types.Part(text="not an image"))
        r = _client().get(f"/api/browser/artefacts/{_ARTEFACT}")
    assert r.status_code == 404
