"""Local end-to-end verification of the document-to-AI pipeline.

Tests the full chain:
  1. make_document_loader writes a doc artifact on the first callback turn
  2. ADK InMemoryArtifactService stores it
  3. The artifact is readable back (simulating load_artifacts_tool retrieval)

No GCP credentials required — uses InMemory backends.

Usage:
    cd backend && uv run python scripts/verify_doc_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

_BUILD_DOC_CTX = "tools.documents.context.build_document_context"


async def main() -> None:
    from google.adk.artifacts import InMemoryArtifactService

    from adk.callbacks import _STATE_DOC_LOAD_ERROR, _STATE_DOCS_LOADED, make_document_loader

    print("=== Document-to-AI pipeline local verification ===\n")

    # --- Sample blocks (what AILANG Parse produces) ---
    sample_blocks = [
        {"type": "heading", "text": "Introduction", "page": 1, "block_id": "b1", "heading_path": ["Introduction"]},
        {"type": "paragraph", "text": "This is the introduction section.", "page": 1, "block_id": "b2"},
        {"type": "heading", "text": "Methodology", "page": 2, "block_id": "b3", "heading_path": ["Methodology"]},
        {"type": "paragraph", "text": "We used qualitative analysis.", "page": 2, "block_id": "b4"},
    ]

    # --- Build a fake CallbackContext wired to InMemoryArtifactService ---
    artifact_svc = InMemoryArtifactService()
    state: dict = {"document_ids": ["test-doc-001"]}

    class FakeSession:
        id = "test-session-001"

    class FakeCtx:
        def __init__(self):
            self.state = state
            self.session = FakeSession()

        async def save_artifact(self, filename: str, artifact, **_kw):
            version = await artifact_svc.save_artifact(
                app_name="aitana_platform",
                user_id="test-user",
                session_id="test-session-001",
                filename=filename,
                artifact=artifact,
            )
            print(f"  save_artifact({filename!r}) → version {version}")
            return version

    ctx = FakeCtx()
    loader = make_document_loader()

    # --- Turn 1: should load the document ---
    print("Turn 1 (first turn — should load document):")
    with patch(_BUILD_DOC_CTX, return_value=("ignored", sample_blocks)):
        await loader(ctx)

    assert state.get(_STATE_DOCS_LOADED) == ["test-doc-001"], "docs_loaded list incorrect"
    assert _STATE_DOC_LOAD_ERROR not in state, f"unexpected error: {state.get(_STATE_DOC_LOAD_ERROR)}"
    print(f"  app:docs_loaded = {state[_STATE_DOCS_LOADED]}")
    print("  PASS: document loaded on first turn\n")

    # --- Turn 2: should skip (already loaded) ---
    print("Turn 2 (second turn — should skip):")
    with patch(_BUILD_DOC_CTX, return_value=("ignored", sample_blocks)) as mock_ctx:
        await loader(ctx)
        assert mock_ctx.call_count == 0, "build_document_context called on second turn"
    print("  PASS: build_document_context not called again\n")

    # --- Verify artifact is readable (what load_artifacts_tool does) ---
    print("Artifact readback (simulating load_artifacts_tool):")
    artifact = await artifact_svc.load_artifact(
        app_name="aitana_platform",
        user_id="test-user",
        session_id="test-session-001",
        filename="doc:test-doc-001.json",
    )
    assert artifact is not None, "artifact not found"
    assert artifact.inline_data.mime_type == "application/json"
    blocks_back = json.loads(artifact.inline_data.data)
    assert blocks_back == sample_blocks, "blocks content mismatch"
    print(f"  MIME type: {artifact.inline_data.mime_type}")
    print(f"  Blocks decoded: {len(blocks_back)} blocks")
    print(f"  First block: {blocks_back[0]['text']!r}")
    print("  PASS: artifact readable and content correct\n")

    # --- Error handling: Firestore unavailable ---
    print("Error handling (Firestore failure — should set per-doc error, not raise):")
    error_state: dict = {"document_ids": ["bad-doc"]}
    error_ctx = FakeCtx()
    error_ctx.state = error_state
    with patch(_BUILD_DOC_CTX, side_effect=RuntimeError("Firestore unavailable")):
        await loader(error_ctx)  # must not raise
    assert error_state.get(_STATE_DOCS_LOADED) == ["bad-doc"]
    errors = error_state.get(_STATE_DOC_LOAD_ERROR, {})
    assert "Firestore unavailable" in errors.get("bad-doc", "")
    print(f"  app:doc_load_error = {errors!r}")
    print("  PASS: error captured in state, no exception raised\n")

    print("=== All checks passed ✓ ===")
    print("\nNote: to test with real Firestore, run with ADC configured and")
    print("call build_document_context with a real parsed document ID.")


if __name__ == "__main__":
    asyncio.run(main())
