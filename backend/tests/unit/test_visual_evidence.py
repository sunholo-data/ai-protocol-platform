"""Security and actual multimodal delivery for the synthetic-only spike."""

import base64
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from google.adk.models.base_llm import BaseLlm
from google.genai import types
from PIL import Image

from auth import User
from tools import visual_evidence as ve

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"></svg>'
REVISION = hashlib.sha256(SVG.encode()).hexdigest()


def png():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("VISUAL_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("VISUAL_EVIDENCE_ALLOWED_UIDS", "alice")
    monkeypatch.setenv("VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS", REVISION)
    monkeypatch.setenv("VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS", f"{REVISION}:{hashlib.sha256(png()).hexdigest()}")
    event = SimpleNamespace(
        id="event-1",
        author="assistant",
        partial=False,
        content=types.Content(role="model", parts=[types.Part.from_text(text=f"```svg\n{SVG}\n```")]),
    )
    service = SimpleNamespace(get_session=AsyncMock(return_value=SimpleNamespace(events=[event])))
    monkeypatch.setattr(ve, "get_session_service", lambda: service)
    return service


def body(**overrides):
    return ve.InspectRequest(
        session_id="session", source_revision=REVISION, png_base64=base64.b64encode(png()).decode(), **overrides
    )


@pytest.mark.asyncio
async def test_default_off(monkeypatch):
    monkeypatch.delenv("VISUAL_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_other_user_refused(configured):
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="bob"))
    assert error.value.status_code == 403
    configured.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_missing_and_stale_source(configured):
    configured.get_session.return_value = None
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 404
    configured.get_session.return_value = SimpleNamespace(events=[])
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_native_png_request_and_no_bytes_response(configured, monkeypatch):
    seen = []

    class Model(BaseLlm):
        model: str = "gemini-test"

        async def generate_content_async(self, request, stream=False):
            seen.append(request)
            yield SimpleNamespace(
                partial=False,
                error_code=None,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text='{"observations":["White square"],"limitations":["Client supplied pixels"]}'
                        )
                    ],
                ),
            )

    monkeypatch.setattr(ve, "approved_model", lambda: (Model(), ["gemini-test@eu"]))
    result = await ve.inspect_asset(body(), User(uid="alice"))
    assert seen[0].contents[0].parts[1].inline_data.data == png()
    assert seen[0].contents[0].parts[1].inline_data.mime_type == "image/png"
    assert result["status"] == "inspected"
    assert result["source_event_id"] == "event-1"
    assert result["capture_digest"] == hashlib.sha256(png()).hexdigest()
    assert "png_base64" not in result
    configured.get_session.assert_awaited_with(app_name=ve.APP_NAME, user_id="alice", session_id="session")


def test_invalid_png():
    with pytest.raises(HTTPException):
        ve.decode_png(base64.b64encode(b"not a PNG").decode())


def test_all_rungs_require_approval(monkeypatch):
    from google.adk.models.google_llm import Gemini

    model = Gemini(model="gemini-test")
    monkeypatch.setenv("VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS", "gemini-test@europe-west1")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    ve.validate_model_members([model])
    with pytest.raises(HTTPException):
        ve.validate_model_members([model, Gemini(model="gemini-unapproved")])
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    with pytest.raises(HTTPException):
        ve.validate_model_members([model])


@pytest.mark.asyncio
async def test_synthetic_gate_and_user_content_cannot_authorize(configured, monkeypatch):
    monkeypatch.setenv("VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS", "")
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 403
    configured.get_session.assert_not_called()
    monkeypatch.setenv("VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS", REVISION)
    configured.get_session.return_value.events[0].author = "user"
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_owner_service_exception_fails_closed(configured):
    configured.get_session.side_effect = PermissionError("Secret owner details")
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 404
    assert "Secret" not in error.value.detail


@pytest.mark.asyncio
async def test_failed_assessment_is_not_inspected(configured, monkeypatch):
    class Model(BaseLlm):
        model: str = "gemini-test"

        async def generate_content_async(self, request, stream=False):
            yield SimpleNamespace(partial=False, error_code="failed", content=None)

    monkeypatch.setattr(ve, "approved_model", lambda: (Model(), ["gemini-test@eu"]))
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 502


def test_no_developer_api_or_non_google_recipient(monkeypatch):
    from google.adk.models.google_llm import Gemini

    monkeypatch.setenv("VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS", "gemini-test@eu")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "eu")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    with pytest.raises(HTTPException):
        ve.validate_model_members([Gemini(model="gemini-test")])
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    with pytest.raises(HTTPException):
        ve.validate_model_members([SimpleNamespace(model="gemini-test")])


def test_png_pixel_limit(monkeypatch):
    monkeypatch.setattr(ve, "MAX_PIXELS", 10)
    with pytest.raises(HTTPException):
        ve.decode_png(base64.b64encode(png()).decode())


def test_approved_model_uses_resolved_chain_and_checks_every_member(monkeypatch):
    import sys
    from unittest.mock import Mock

    from google.adk.models.google_llm import Gemini

    from adk.resilient_llm import ResilientLlm
    from config import models

    member = Gemini(model="gemini-test")
    resolver = Mock(return_value=member)
    monkeypatch.setitem(sys.modules, "adk.agent", SimpleNamespace(resolve_model_chain=resolver))
    monkeypatch.setattr(
        models, "entry_for", lambda ref: SimpleNamespace(id="fixture-model", provider="google", residency="eu")
    )
    monkeypatch.setenv("VISUAL_EVIDENCE_MODEL", "fixture-model")
    monkeypatch.setenv("VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS", "gemini-test@eu")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "eu")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    model, labels = ve.approved_model()
    resolver.assert_called_once_with("fixture-model")
    assert isinstance(model, ResilientLlm)
    assert labels == ["gemini-test@eu"]
    resolver.return_value = ResilientLlm(chain=[member, Gemini(model="gemini-unapproved")])
    with pytest.raises(HTTPException):
        ve.approved_model()
    monkeypatch.delenv("VISUAL_EVIDENCE_MODEL")
    with pytest.raises(HTTPException):
        ve.approved_model()


@pytest.mark.asyncio
async def test_route_bounds_and_private_response(configured, monkeypatch):
    import httpx
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(ve.router)
    app.dependency_overrides[ve.get_current_user] = lambda: User(uid="alice")
    monkeypatch.setattr(ve, "inspect_asset", AsyncMock(return_value={"status": "inspected"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/visual-evidence/inspect", json=body().model_dump())
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"
        invalid = await client.post("/api/visual-evidence/inspect", json={"png_base64": "private-secret"})
        assert invalid.status_code == 422
        assert "private-secret" not in invalid.text
        monkeypatch.setattr(ve, "MAX_REQUEST_BYTES", 10)
        oversized = await client.post("/api/visual-evidence/inspect", content=b"x" * 11)
        assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_unapproved_pixels_never_reach_model(configured, monkeypatch):
    gate = AsyncMock()
    monkeypatch.setattr(ve, "approved_model", gate)
    monkeypatch.setenv("VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS", "")
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 403
    gate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setting",
    [
        "VISUAL_EVIDENCE_ENABLED",
        "VISUAL_EVIDENCE_ALLOWED_UIDS",
        "VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS",
        "VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS",
    ],
)
async def test_revocation_during_assessment_is_refused(configured, monkeypatch, setting):
    class Model(BaseLlm):
        model: str = "gemini-test"

        async def generate_content_async(self, request, stream=False):
            monkeypatch.setenv(setting, "")
            yield SimpleNamespace(
                partial=False,
                error_code=None,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text='{"observations":[],"limitations":[]}')]
                ),
            )

    monkeypatch.setattr(ve, "approved_model", lambda: (Model(), ["gemini-test@eu"]))
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_model_approval_revocation_before_return(configured, monkeypatch):
    from unittest.mock import Mock

    class Model(BaseLlm):
        model: str = "gemini-test"

        async def generate_content_async(self, request, stream=False):
            yield SimpleNamespace(
                partial=False,
                error_code=None,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text='{"observations":[],"limitations":[]}')]
                ),
            )

    gate = Mock(side_effect=[(Model(), ["gemini-test@eu"]), HTTPException(503, "Recipient revoked")])
    monkeypatch.setattr(ve, "approved_model", gate)
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 503
    assert gate.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_user_inspection_refused_and_failure_releases_slot(configured, monkeypatch):
    import asyncio

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def fail_after_wait(body, user):
        entered.set()
        await finish.wait()
        raise HTTPException(502, "Inspection failed")

    monkeypatch.setattr(ve, "_inspect_asset", fail_after_wait)
    first = asyncio.create_task(ve.inspect_asset(body(), User(uid="alice")))
    await entered.wait()
    with pytest.raises(HTTPException) as error:
        await ve.inspect_asset(body(), User(uid="alice"))
    assert error.value.status_code == 429
    finish.set()
    with pytest.raises(HTTPException):
        await first
    monkeypatch.setattr(ve, "_inspect_asset", AsyncMock(return_value={"status": "inspected"}))
    assert await ve.inspect_asset(body(), User(uid="alice")) == {"status": "inspected"}
