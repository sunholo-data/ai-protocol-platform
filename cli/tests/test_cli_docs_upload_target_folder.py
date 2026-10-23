"""`docs upload FILE --folder NAME` — the name resolves to the caller's folder,
is created when no folder has it, and is never sent as a raw id.

Found 2026-10-15: an unknown name passed straight through as ``folder_id``, the
backend stored it verbatim, and the document was an orphan no folder listed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
from click.testing import CliRunner

from aiplatform.commands import docs as docs_mod

_UUID_A = "11111111-2222-3333-4444-555555555555"
_UUID_B = "66666666-7777-8888-9999-000000000000"


def _run(tmp_path, monkeypatch, folders: list[dict], args: list[str], create_response: dict | None = None):
    sample = tmp_path / "note.docx"
    sample.write_bytes(b"x")
    posted: list[dict] = []

    def _fake_post(url, **kw):
        posted.append(dict(kw.get("data") or {}))
        return httpx.Response(200, json={"status": "parsed", "blocksCount": 1})

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = MagicMock()
    client.base_url = "http://backend"
    client._auth_headers.return_value = {}
    client.get.side_effect = lambda path: {"folders": folders} if path == "/api/folders" else {}
    client.post.return_value = create_response
    monkeypatch.setattr(docs_mod, "_client", lambda _ctx: client)
    result = CliRunner().invoke(docs_mod.docs, ["upload", str(sample), *args])
    return result, posted, client


def test_existing_name_uploads_into_it_without_creating(tmp_path, monkeypatch):
    folders = [{"id": _UUID_A, "name": "Contracts"}]
    result, posted, client = _run(tmp_path, monkeypatch, folders, ["--folder", " contracts "])
    assert result.exit_code == 0, result.output
    assert posted[0]["folder_id"] == _UUID_A
    client.post.assert_not_called()
    assert "Created folder" not in result.output


def test_unknown_name_creates_the_folder_and_says_so(tmp_path, monkeypatch):
    result, posted, client = _run(
        tmp_path,
        monkeypatch,
        [{"id": _UUID_A, "name": "Contracts"}],
        ["--folder", "Board Papers"],
        create_response={"id": _UUID_B, "name": "Board Papers"},
    )
    assert result.exit_code == 0, result.output
    client.post.assert_called_once_with("/api/folders", json={"name": "Board Papers"})
    assert "Created folder 'Board Papers'." in result.output
    assert posted[0]["folder_id"] == _UUID_B, "the upload must target the NEW folder's id, not the name"


def test_raw_folder_id_still_works(tmp_path, monkeypatch):
    folders = [{"id": _UUID_A, "name": "Contracts"}]
    result, posted, client = _run(tmp_path, monkeypatch, folders, ["--folder", _UUID_A])
    assert result.exit_code == 0, result.output
    assert posted[0]["folder_id"] == _UUID_A
    client.post.assert_not_called()


def test_unknown_uuid_is_an_error_not_a_new_folder(tmp_path, monkeypatch):
    result, posted, client = _run(tmp_path, monkeypatch, [{"id": _UUID_A, "name": "Contracts"}], ["--folder", _UUID_B])
    assert result.exit_code != 0
    assert f"No folder with id '{_UUID_B}'" in result.output
    client.post.assert_not_called()
    assert posted == []


def test_ambiguous_name_fails_listing_every_match(tmp_path, monkeypatch):
    folders = [
        {"id": _UUID_A, "name": "Contracts", "docCount": 3},
        {"id": _UUID_B, "name": "contracts", "docCount": 0},
    ]
    result, posted, client = _run(tmp_path, monkeypatch, folders, ["--folder", "Contracts"])
    assert result.exit_code != 0
    assert "2 folders are called 'Contracts'" in result.output
    assert _UUID_A in result.output and _UUID_B in result.output
    client.post.assert_not_called()
    assert posted == []


def test_create_without_an_id_fails_visibly(tmp_path, monkeypatch):
    result, posted, _ = _run(tmp_path, monkeypatch, [], ["--folder", "New"], create_response={})
    assert result.exit_code != 0
    assert "returned no id" in result.output
    assert posted == []
