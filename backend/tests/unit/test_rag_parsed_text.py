"""RAG consumes authorised rendered text, never a source PDF or public export."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag import parsed_text as rag_text
from tools.documents.parsed_text import parsed_text_from_record


@pytest.fixture
def imports(monkeypatch):
    receipts = {}
    monkeypatch.setattr(rag_text, "get_document", lambda collection, key: receipts.get(key))
    monkeypatch.setattr(
        rag_text, "set_document", lambda collection, key, value, **kw: receipts.setdefault(key, {}).update(value)
    )
    doc = {
        "userId": "alice",
        "parseStatus": "parsed",
        "originalFilename": "Contract.pdf",
        "sourceUrl": "gs://restricted-original/contract.pdf",
        "blocks": [{"type": "paragraph", "text": "Price 63.40"}],
    }
    monkeypatch.setattr(
        rag_text,
        "load_parsed_text",
        lambda doc_id, caller_uid: parsed_text_from_record(doc_id, doc, caller_uid=caller_uid),
    )
    paths = []
    bodies = []

    def upload(corpus, path, display):
        paths.append(Path(path))
        bodies.append(Path(path).read_text())
        assert path.endswith(".txt") and display.endswith(".txt")
        return SimpleNamespace(name=f"{corpus}/ragFiles/{len(paths)}")

    uploaded = Mock(side_effect=upload)
    deleted = Mock()
    monkeypatch.setattr(rag_text, "_upload_file_sync", uploaded)
    monkeypatch.setattr(rag_text, "_delete_file_sync", deleted)
    return doc, uploaded, deleted, paths, bodies


@pytest.mark.asyncio
async def test_imports_text_and_reuses_receipt(imports):
    _doc, uploaded, deleted, paths, bodies = imports
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    assert uploaded.call_count == 1 and not deleted.called
    assert bodies == ["Price 63.40"] and not paths[0].exists()
    assert "gs://" not in bodies[0]


@pytest.mark.asyncio
async def test_changed_text_replaces_prior_file(imports):
    doc, uploaded, deleted, _paths, bodies = imports
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    doc["editedBlocks"] = {"0": {"editedText": "Price 71.25"}}
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    assert uploaded.call_count == 2 and bodies == ["Price 63.40", "Price 71.25"]
    deleted.assert_called_once_with("corpus/1/ragFiles/1")


@pytest.mark.asyncio
async def test_unreadable_or_failed_parse_never_uploads_original(imports):
    doc, uploaded, *_ = imports
    with pytest.raises(PermissionError):
        await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="bob")
    doc["parseStatus"] = "failed"
    with pytest.raises(ValueError):
        await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    assert not uploaded.called


@pytest.mark.asyncio
async def test_renamed_document_updates_search_citation(imports):
    doc, uploaded, deleted, _paths, _bodies = imports
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    doc["originalFilename"] = "Renamed contract.pdf"
    await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    assert uploaded.call_count == 2 and deleted.called
    assert uploaded.call_args.args[2] == "Renamed contract.pdf (parsed text).txt"


@pytest.mark.asyncio
async def test_temp_text_removed_when_upload_fails(imports):
    _doc, uploaded, _deleted, paths, _bodies = imports

    def fail(corpus, path, display):
        paths.append(Path(path))
        raise RuntimeError("index unavailable")

    uploaded.side_effect = fail
    with pytest.raises(RuntimeError):
        await rag_text.import_parsed_document("corpus/1", "doc-a", caller_uid="alice")
    assert not paths[0].exists()
