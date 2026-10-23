"""POST /{doc_id}/revise-passage — targeted passage revision.

Added 2026-09-01 after the first real use on test. Editing one sentence went
through a full-document regeneration: ~2 minutes on a 91-block file, and the
model had to reproduce every other paragraph verbatim to leave them alone.

Splicing removes both problems. The model sees ONLY the passage, and the rest of
the document is untouched **by construction** rather than by the model behaving.
That is what these tests pin — especially `locate_passage`, because a wrong
match would edit the wrong paragraph, which is worse than refusing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from tools.documents.generate import locate_passage

_OWNER = User(uid="user1", email="a@b.com", domain="b.com")

_SOURCE = """# Commercial Offer

## Summary

The term is ten years and may be extended by mutual agreement.

## Pricing

- Fixed price for the first three years
- Indexed thereafter
"""

_DOC = {
    "userId": "user1",
    "originalFilename": "Offer.docx",
    "sourceFormat": "docx",
    "folderId": "created",
    "sourceMarkdown": _SOURCE,
}


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.generate import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _OWNER
    return TestClient(app)


class _Response:
    doc_id, status, original_filename = "doc-1", "parsed", "Offer.docx"
    blocks_count, storage_path, folder_id, error = 9, "p", "created", None


def _patches(doc=_DOC, rewritten="The term is ten years.", capture=None):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("db.firestore.get_document", return_value=doc))

    async def fake_rewrite(passage, instruction):
        if capture is not None:
            capture["passage"] = passage
            capture["instruction"] = instruction
        return rewritten

    async def fake_generate(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return _Response()

    stack.enter_context(patch("tools.documents.generate.rewrite_passage", side_effect=fake_rewrite))
    stack.enter_context(patch("tools.documents.generate._generate_into_workspace", side_effect=fake_generate))
    return stack


class TestLocatePassage:
    def test_finds_an_exact_match(self):
        assert locate_passage(_SOURCE, "The term is ten years and may be extended by mutual agreement.")

    def test_finds_a_selection_whose_whitespace_was_collapsed(self):
        """Selecting rendered text collapses newlines; the source has them."""
        found = locate_passage("a\nline   here", "a line here")
        assert found == "a\nline   here"

    def test_returns_the_original_slice_not_the_normalised_one(self):
        """The splice must put back exactly what was there."""
        src = "- Fixed price for the first three years\n- Indexed thereafter"
        found = locate_passage(src, "- Fixed price for the first three years - Indexed thereafter")
        assert found == src

    def test_a_passage_that_is_not_there_returns_none(self):
        assert locate_passage(_SOURCE, "no such sentence anywhere") is None

    def test_empty_inputs_return_none(self):
        assert locate_passage(_SOURCE, "") is None
        assert locate_passage("", "anything") is None


class TestSplice:
    def test_only_the_selected_passage_changes(self, client: TestClient):
        captured: dict = {}
        with _patches(rewritten="The term is ten years.", capture=captured):
            resp = client.post(
                "/api/documents/doc-1/revise-passage",
                json={
                    "anchorText": "The term is ten years and may be extended by mutual agreement.",
                    "instruction": "shorter",
                },
            )

        assert resp.status_code == 200
        spliced = captured["content_markdown"]
        assert "The term is ten years." in spliced
        assert "may be extended by mutual agreement" not in spliced
        # Everything else is byte-identical.
        assert "# Commercial Offer" in spliced
        assert "- Fixed price for the first three years" in spliced
        assert "- Indexed thereafter" in spliced

    def test_the_model_only_sees_the_passage(self, client: TestClient):
        """The saving in both time and risk: the document never reaches the model."""
        captured: dict = {}
        with _patches(capture=captured):
            client.post(
                "/api/documents/doc-1/revise-passage",
                json={
                    "anchorText": "The term is ten years and may be extended by mutual agreement.",
                    "instruction": "shorter",
                },
            )

        assert captured["passage"] == "The term is ten years and may be extended by mutual agreement."
        assert "Commercial Offer" not in captured["passage"]
        assert "Fixed price" not in captured["passage"]

    def test_it_updates_in_place(self, client: TestClient):
        captured: dict = {}
        with _patches(capture=captured):
            client.post(
                "/api/documents/doc-1/revise-passage",
                json={
                    "anchorText": "The term is ten years and may be extended by mutual agreement.",
                    "instruction": "x",
                },
            )

        assert captured["filename"] == "Offer.docx"
        assert captured["folder_id"] == "created"
        assert captured["via"] == "revise-passage"

    def test_only_the_first_occurrence_is_replaced(self, client: TestClient):
        doc = {**_DOC, "sourceMarkdown": "repeat me\n\nrepeat me"}
        captured: dict = {}
        with _patches(doc=doc, rewritten="changed", capture=captured):
            client.post(
                "/api/documents/doc-1/revise-passage",
                json={"anchorText": "repeat me", "instruction": "x"},
            )

        assert captured["content_markdown"] == "changed\n\nrepeat me"


class TestRefusals:
    def test_a_passage_it_cannot_find_is_refused_not_guessed(self, client: TestClient):
        """A wrong splice edits the wrong paragraph — worse than refusing."""
        with _patches():
            resp = client.post(
                "/api/documents/doc-1/revise-passage",
                json={"anchorText": "text that is not in the document", "instruction": "x"},
            )

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "PASSAGE_NOT_FOUND"

    def test_an_uploaded_document_has_no_editable_source(self, client: TestClient):
        with _patches(doc={**_DOC, "sourceMarkdown": ""}):
            resp = client.post(
                "/api/documents/doc-1/revise-passage",
                json={"anchorText": "anything", "instruction": "x"},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NO_EDITABLE_SOURCE"

    def test_an_empty_rewrite_leaves_the_document_alone(self, client: TestClient):
        captured: dict = {}
        with _patches(rewritten="   ", capture=captured):
            resp = client.post(
                "/api/documents/doc-1/revise-passage",
                json={
                    "anchorText": "The term is ten years and may be extended by mutual agreement.",
                    "instruction": "x",
                },
            )

        assert resp.status_code == 502
        assert "content_markdown" not in captured, "nothing should have been regenerated"

    def test_a_non_owner_gets_403(self, client: TestClient):
        with _patches(doc={**_DOC, "userId": "someone-else"}):
            resp = client.post(
                "/api/documents/doc-1/revise-passage",
                json={"anchorText": "x", "instruction": "y"},
            )
        assert resp.status_code == 403

    def test_a_non_owner_never_reaches_the_model(self, client: TestClient):
        captured: dict = {}
        with _patches(doc={**_DOC, "userId": "other"}, capture=captured):
            client.post(
                "/api/documents/doc-1/revise-passage",
                json={"anchorText": "x", "instruction": "y"},
            )
        assert captured == {}

    def test_unknown_document_is_404(self, client: TestClient):
        with _patches(doc=None):
            resp = client.post(
                "/api/documents/nope/revise-passage",
                json={"anchorText": "x", "instruction": "y"},
            )
        assert resp.status_code == 404
