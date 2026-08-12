"""Tests for A2A FilePart extraction via ExecuteInterceptor (G46 M1).

Five tests covering the contract M1 of Sprint A2A-DOCS establishes:

  1. force_new_version regression guard — `build_a2a_app()` constructs
     `A2aAgentExecutor` with `force_new_version=True`. WITHOUT this flag,
     ADK's executor silently picks the LEGACY path that bypasses
     interceptors entirely — code reads correct, tests for the
     interceptor function pass in isolation, but production silently drops
     every uploaded file. Real failure 2026-06-08T04:45 UTC on the
     gde-ap-agent fork; cost ~90 min to diagnose. This test catches it
     BEFORE production.

  2. FileWithBytes extraction — FilePart with inline bytes is persisted
     as `doc:{id}.json` artifact + appended to `state["document_ids"]`.

  3. FilePart double-injection guard — the FilePart is REMOVED from
     `message.parts` so ADK's native `convert_a2a_part_to_genai_part`
     converter doesn't also send the file directly to Gemini multimodal
     (which would defeat our doc-loader pipeline).

  4. Disabled-mode no-op — when `ENABLE_A2A_FILE_INPUT` is unset,
     the interceptor is a pure pass-through (clean-rollback contract).

  5. Validation rejections — oversized file (size cap) + bad URI scheme
     (file://) + unknown MIME — each rejected with a synthetic TextPart
     explaining the reason so the peer sees useful feedback rather than
     silent drop.

The interceptor needs a real Runner + session_service + artifact_service
to inject state. Tests use a minimal-LlmAgent + InMemory services pattern
(mirrors test_a2a_invocation.test_build_a2a_app_returns_mountable_starlette_app).
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest


def _build_runner() -> Any:
    """Minimal Runner backed by InMemorySessionService + InMemoryArtifactService.

    Same pattern as `test_a2a_invocation.test_build_a2a_app_returns_mountable_starlette_app`
    — never actually invokes Gemini; we only exercise the interceptor's
    state + artifact effects.
    """
    from google.adk.agents import LlmAgent
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    agent = LlmAgent(
        name="probe_agent",
        model="gemini-2.5-flash",
        description="probe",
        instruction="probe",
    )
    return Runner(
        app_name="test_a2a_files",
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )


def _build_context(parts: list[Any], context_id: str = "test-session-1") -> Any:
    """Construct a minimal RequestContext with the given parts.

    a2a-sdk's RequestContext takes a MessageSendParams; we build a
    Message and wrap it. Real A2A peers send through the same path.
    """
    import uuid

    from a2a.server.agent_execution.context import RequestContext
    from a2a.types import Message, MessageSendParams

    msg = Message(
        message_id=str(uuid.uuid4()),
        role="user",
        parts=parts,
        context_id=context_id,
    )
    params = MessageSendParams(message=msg)
    return RequestContext(request=params, context_id=context_id)


def _text_part(text: str) -> Any:
    from a2a.types import Part, TextPart

    return Part(root=TextPart(text=text))


def _file_with_bytes_part(decoded: bytes, *, mime_type: str, name: str = "test.bin") -> Any:
    from a2a.types import FilePart, FileWithBytes, Part

    return Part(
        root=FilePart(
            file=FileWithBytes(
                bytes=base64.b64encode(decoded).decode("ascii"),
                mime_type=mime_type,
                name=name,
            )
        )
    )


def _file_with_uri_part(uri: str, *, mime_type: str | None = None, name: str = "test.pdf") -> Any:
    from a2a.types import FilePart, FileWithUri, Part

    return Part(root=FilePart(file=FileWithUri(uri=uri, mime_type=mime_type, name=name)))


# ---------------------------------------------------------------------------
# Test 1 — Friction 29 regression guard (THE most important test in this sprint)
# ---------------------------------------------------------------------------


def test_build_a2a_app_constructs_executor_with_force_new_version_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for Friction 29.

    Without `force_new_version=True`, ADK's `A2aAgentExecutor` picks the
    LEGACY impl path that bypasses interceptors entirely. Code looks
    correct, tests for the interceptor function in isolation pass,
    production silently drops every uploaded file.

    Real failure 2026-06-08T04:45 UTC on gde-ap-agent — cost ~90 min to
    diagnose. If a future refactor removes the flag, this test fails
    BEFORE the interceptor goes inert in production.

    Implementation note: we introspect the constructed executor's
    `_force_new_version` attribute (ADK's public-by-mistake instance
    field). If ADK renames it in a future release this test will fail
    loudly, which is the right outcome — the contract this test
    enforces is "the new-impl path is forced", and verifying that needs
    SOME hook into the executor's state.
    """
    monkeypatch.setenv("A2A_INVOCATION_REQUIRE_AUTH", "false")

    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.agents import LlmAgent

    from protocols.a2a_invocation import build_a2a_app

    agent = LlmAgent(
        name="probe_agent",
        model="gemini-2.5-flash",
        description="probe agent for force_new_version regression guard",
        instruction="probe — never reached during this test",
    )

    # Capture all A2aAgentExecutor constructions so we can assert on the
    # one build_a2a_app makes. We can't easily reach into the built
    # Starlette app to find the executor instance, so spy at construction.
    captured: list[A2aAgentExecutor] = []
    real_init = A2aAgentExecutor.__init__

    def _spy_init(self: A2aAgentExecutor, **kwargs: Any) -> None:
        real_init(self, **kwargs)
        captured.append(self)

    monkeypatch.setattr(A2aAgentExecutor, "__init__", _spy_init)

    build_a2a_app(agent, "https://example.com")

    assert len(captured) >= 1, "build_a2a_app must construct at least one A2aAgentExecutor"
    executor = captured[-1]
    assert executor._force_new_version is True, (
        "FRICTION 29 REGRESSION: A2aAgentExecutor was constructed WITHOUT "
        "force_new_version=True. The LEGACY impl path will silently bypass "
        "the FileExtractionInterceptor — every A2A FilePart will be dropped "
        "before reaching the agent. See sprint_A2A-DOCS notes."
    )


# ---------------------------------------------------------------------------
# Test 2 — FileWithBytes extracted + artifact saved + state populated
# ---------------------------------------------------------------------------


def test_a2a_file_with_bytes_extracted_to_document_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FileWithBytes part is extracted; a document_id is minted; an
    artifact is saved; state["document_ids"] is populated.

    This is the happy-path contract for Scenario A (file-inbound via GE).
    """
    monkeypatch.setenv("ENABLE_A2A_FILE_INPUT", "true")

    from protocols.file_extraction import make_file_extraction_interceptor

    runner = _build_runner()
    interceptor = make_file_extraction_interceptor(runner, app_name="test_a2a_files")
    context = _build_context(
        [
            _text_part("Process this invoice"),
            _file_with_bytes_part(b"%PDF-1.4 fake invoice", mime_type="application/pdf", name="acme.pdf"),
        ]
    )

    new_context = asyncio.run(interceptor.before_agent(context))

    # FilePart was stripped; only the text part remains.
    parts = new_context.message.parts
    assert len(parts) == 1, f"expected 1 part after strip, got {len(parts)}: {parts!r}"

    # Session created with document_ids — interceptor derives user_id
    # from the context_id (matches ADK's request_converter convention).
    expected_user = "A2A_USER_test-session-1"
    session = asyncio.run(
        runner.session_service.get_session(
            app_name="test_a2a_files", user_id=expected_user, session_id="test-session-1"
        )
    )
    assert session is not None, "interceptor must create the session if missing"
    doc_ids = session.state.get("document_ids", [])
    assert len(doc_ids) == 1, f"expected 1 document_id, got {doc_ids!r}"

    # Artifact was saved with the deterministic doc:{id}.json filename.
    doc_id = doc_ids[0]
    artifact = asyncio.run(
        runner.artifact_service.load_artifact(
            app_name="test_a2a_files",
            user_id=expected_user,
            session_id="test-session-1",
            filename=f"doc:{doc_id}.json",
        )
    )
    assert artifact is not None, "artifact_service must have the doc:{id}.json blob"


# ---------------------------------------------------------------------------
# Test 3 — FilePart double-injection guard
# ---------------------------------------------------------------------------


def test_a2a_file_part_stripped_from_message_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """FilePart is REMOVED from message.parts after extraction.

    Without this strip, ADK's `convert_a2a_part_to_genai_part` would
    ALSO inject the raw bytes into Gemini as native multimodal `inline_data`
    — defeating the doc-loader pipeline (orchestrator instructions
    reference `state["document_ids"]`, not raw inline parts) and
    double-counting the file in token usage.
    """
    monkeypatch.setenv("ENABLE_A2A_FILE_INPUT", "true")

    from a2a.types import FilePart, TextPart

    from protocols.file_extraction import make_file_extraction_interceptor

    runner = _build_runner()
    interceptor = make_file_extraction_interceptor(runner, app_name="test_a2a_files")
    context = _build_context(
        [
            _text_part("Process this"),
            _file_with_bytes_part(b"data", mime_type="application/pdf", name="a.pdf"),
            _file_with_bytes_part(b"data2", mime_type="application/pdf", name="b.pdf"),
        ],
        context_id="test-session-strip",
    )

    new_context = asyncio.run(interceptor.before_agent(context))

    # Both FileParts stripped; only the text part remains.
    parts = new_context.message.parts
    assert len(parts) == 1, f"expected only 1 (text) part after strip, got {len(parts)}"
    root = getattr(parts[0], "root", parts[0])
    assert isinstance(root, TextPart), f"surviving part must be the original TextPart, got {type(root).__name__}"

    # Defence-in-depth: no FilePart anywhere in surviving parts.
    for p in parts:
        r = getattr(p, "root", p)
        assert not isinstance(r, FilePart), f"FilePart leaked through: {r!r}"


# ---------------------------------------------------------------------------
# Test 4 — Disabled-mode no-op (clean-rollback contract)
# ---------------------------------------------------------------------------


def test_a2a_interceptor_is_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ENABLE_A2A_FILE_INPUT` unset → interceptor is a pure pass-through.

    Even with FilePart present, no session is created, no artifact saved,
    no part stripped. Byte-identical to the no-interceptor baseline.
    """
    monkeypatch.delenv("ENABLE_A2A_FILE_INPUT", raising=False)

    from protocols.file_extraction import make_file_extraction_interceptor

    runner = _build_runner()
    interceptor = make_file_extraction_interceptor(runner, app_name="test_a2a_files")
    context = _build_context(
        [
            _text_part("Process this invoice"),
            _file_with_bytes_part(b"data", mime_type="application/pdf", name="x.pdf"),
        ],
        context_id="test-session-off",
    )
    original_part_count = len(context.message.parts)

    new_context = asyncio.run(interceptor.before_agent(context))

    # Flag off → no mutation, no session.
    assert len(new_context.message.parts) == original_part_count, "flag-off must not strip parts"
    session = asyncio.run(
        runner.session_service.get_session(
            app_name="test_a2a_files",
            user_id="A2A_USER_test-session-off",
            session_id="test-session-off",
        )
    )
    assert session is None, "flag-off must not create session"


# ---------------------------------------------------------------------------
# Test 5 — Validation rejections (MIME mismatch, size cap, bad URI scheme)
# ---------------------------------------------------------------------------


def test_a2a_validation_errors_rejected_with_synthetic_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three failure modes in one test:
      - oversized file (size cap)
      - unknown MIME type
      - disallowed URI scheme (file://)

    Each is rejected without minting a doc_id; the interceptor appends a
    synthetic TextPart with the reason so the peer (and the model) see
    useful feedback rather than silent drop.
    """
    monkeypatch.setenv("ENABLE_A2A_FILE_INPUT", "true")
    monkeypatch.setenv("A2A_FILE_MAX_BYTES", "1024")  # 1 KB cap

    from a2a.types import TextPart

    from protocols.file_extraction import make_file_extraction_interceptor

    # --- oversized FileWithBytes ---
    runner1 = _build_runner()
    interceptor1 = make_file_extraction_interceptor(runner1, app_name="test_a2a_files")
    big_bytes = b"X" * 2048  # 2 KB > 1 KB cap
    context1 = _build_context(
        [_file_with_bytes_part(big_bytes, mime_type="application/pdf", name="big.pdf")],
        context_id="test-session-big",
    )
    out1 = asyncio.run(interceptor1.before_agent(context1))
    parts1 = out1.message.parts
    assert len(parts1) == 1, f"oversize: expected 1 (synthetic) part, got {len(parts1)}"
    root1 = getattr(parts1[0], "root", parts1[0])
    assert isinstance(root1, TextPart)
    assert "big.pdf" in root1.text
    assert "exceeds" in root1.text.lower() or "size" in root1.text.lower()

    # --- unknown MIME ---
    runner2 = _build_runner()
    interceptor2 = make_file_extraction_interceptor(runner2, app_name="test_a2a_files")
    context2 = _build_context(
        [_file_with_bytes_part(b"PK\x03\x04evil", mime_type="application/x-evil", name="evil.bin")],
        context_id="test-session-mime",
    )
    out2 = asyncio.run(interceptor2.before_agent(context2))
    parts2 = out2.message.parts
    assert len(parts2) == 1
    root2 = getattr(parts2[0], "root", parts2[0])
    assert isinstance(root2, TextPart)
    assert "evil.bin" in root2.text
    assert "MIME" in root2.text or "format" in root2.text.lower()

    # --- disallowed URI scheme (file://) ---
    runner3 = _build_runner()
    interceptor3 = make_file_extraction_interceptor(runner3, app_name="test_a2a_files")
    context3 = _build_context(
        [_file_with_uri_part("file:///etc/passwd", mime_type="text/plain", name="passwd")],
        context_id="test-session-scheme",
    )
    out3 = asyncio.run(interceptor3.before_agent(context3))
    parts3 = out3.message.parts
    assert len(parts3) == 1
    root3 = getattr(parts3[0], "root", parts3[0])
    assert isinstance(root3, TextPart)
    assert "passwd" in root3.text
    assert "scheme" in root3.text.lower() or "https" in root3.text.lower()
