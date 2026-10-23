"""`docs list` / `docs status` read the API's envelope, not a bare array.

`/api/folders` returns `_FoldersListResponse` (`{"folders": [...]}`) and
`/api/folders/{id}/documents` returns `_DocumentsListResponse`
(`{"documents": [...]}`). Every reader in commands/docs.py used to test
`isinstance(payload, list)` and fall back to empty, so:

  * `docs list` printed `[]` however many documents existed;
  * `--folder <name>` never resolved — `_resolve_folder` iterated nothing, passed
    the literal name through, and the request 404'd on `/api/folders/Created/...`.

Both read as missing data rather than a client that could not parse the reply,
which is the expensive kind of wrong.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aiplatform.commands.docs import _all_docs, _items, _resolve_folder

_FOLDERS = {"folders": [{"id": "f-uuid-1", "name": "Created"}, {"id": "f-uuid-2", "name": "Uploads"}]}


def _client(responses: dict) -> MagicMock:
    client = MagicMock()
    client.get.side_effect = lambda path: responses[path]
    return client


def test_items_unwraps_the_envelope():
    assert _items(_FOLDERS, "folders") == _FOLDERS["folders"]
    assert _items({"documents": [{"id": "d1"}]}, "documents") == [{"id": "d1"}]


def test_items_tolerates_a_bare_list_and_junk():
    """The envelope is what the API sends today; a bare list must still work so
    this survives a response-model change, and anything else degrades to empty
    rather than raising in the middle of a listing."""
    assert _items([{"id": "d1"}], "documents") == [{"id": "d1"}]
    assert _items({"documents": None}, "documents") == []
    assert _items({"wrong_key": [1]}, "documents") == []
    assert _items(None, "documents") == []


def test_resolve_folder_maps_a_name_to_its_id():
    client = _client({"/api/folders": _FOLDERS})
    assert _resolve_folder(client, "Created") == "f-uuid-1"


def test_resolve_folder_is_case_and_space_insensitive():
    client = _client({"/api/folders": _FOLDERS})
    assert _resolve_folder(client, "  created  ") == "f-uuid-1"


def test_resolve_folder_passes_an_id_straight_through():
    """An unmatched value must survive untouched, or passing a real id breaks."""
    client = _client({"/api/folders": _FOLDERS})
    assert _resolve_folder(client, "f-uuid-2") == "f-uuid-2"
    assert _resolve_folder(client, "no-such-folder") == "no-such-folder"


def test_all_docs_collects_across_folders():
    client = _client(
        {
            "/api/folders": _FOLDERS,
            "/api/folders/f-uuid-1/documents": {"documents": [{"id": "d1"}]},
            "/api/folders/f-uuid-2/documents": {"documents": [{"id": "d2"}, {"id": "d3"}]},
        }
    )
    assert [d["id"] for d in _all_docs(client)] == ["d1", "d2", "d3"]


def test_all_docs_is_empty_only_when_the_folders_are():
    client = _client(
        {
            "/api/folders": _FOLDERS,
            "/api/folders/f-uuid-1/documents": {"documents": []},
            "/api/folders/f-uuid-2/documents": {"documents": []},
        }
    )
    assert _all_docs(client) == []


# ---------------------------------------------------------------------------
# docs upload — takes a folder NAME too (CLAUDE.md #9)
# ---------------------------------------------------------------------------


def test_upload_resolves_a_folder_name_before_posting(tmp_path, monkeypatch):
    """`--folder Contracts` must reach the server as that folder's id.

    Sent unresolved, the backend has no folder by that id and the upload lands
    in the default folder — the same silent misfiling the Form() fix just
    removed, reintroduced one layer up.
    """
    import httpx

    from aiplatform.commands import docs as docs_mod

    sample = tmp_path / "note.docx"
    sample.write_bytes(b"x")

    posted: dict = {}

    def _fake_post(url, **kwargs):
        posted.update(kwargs.get("data") or {})
        return httpx.Response(200, json={"status": "parsed", "blocksCount": 1})

    monkeypatch.setattr(httpx, "post", _fake_post)

    client = _client({"/api/folders": _FOLDERS})
    client.base_url = "http://backend"
    client._auth_headers.return_value = {}
    monkeypatch.setattr(docs_mod, "_client", lambda _ctx: client)

    from click.testing import CliRunner

    result = CliRunner().invoke(docs_mod.docs, ["upload", str(sample), "--folder", "Created"])

    assert result.exit_code == 0, result.output
    assert posted["folder_id"] == "f-uuid-1", f"name was not resolved; sent {posted.get('folder_id')!r}"


def test_upload_without_a_folder_sends_empty_not_a_lookup(tmp_path, monkeypatch):
    """No --folder means 'let the backend pick the default'. It must not become
    a resolve call, which would 404-or-guess its way to some other folder."""
    import httpx

    from aiplatform.commands import docs as docs_mod

    sample = tmp_path / "note.docx"
    sample.write_bytes(b"x")
    posted: dict = {}

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: (posted.update(kw.get("data") or {}), httpx.Response(200, json={"status": "parsed"}))[1],
    )

    client = _client({"/api/folders": _FOLDERS})
    client.base_url = "http://backend"
    client._auth_headers.return_value = {}
    monkeypatch.setattr(docs_mod, "_client", lambda _ctx: client)

    from click.testing import CliRunner

    result = CliRunner().invoke(docs_mod.docs, ["upload", str(sample)])

    assert result.exit_code == 0, result.output
    assert posted["folder_id"] == ""
    client.get.assert_not_called()
