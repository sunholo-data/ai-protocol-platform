"""`docs upload -r DIR` — folder upload keeps subfolders, skips junk, reports
every outcome, and `AIPLATFORM_AUTH=tailnet` sends no token."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import httpx
from click.testing import CliRunner

from aiplatform.commands import docs as docs_mod


def _tree(tmp_path):
    root = tmp_path / "Reports"
    (root / "2026").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "top.pdf").write_bytes(b"a")
    (root / "2026" / "q1.docx").write_bytes(b"b")
    (root / "2026" / "chart.png").write_bytes(b"c")
    (root / "2026" / "data.xyz").write_bytes(b"d")
    (root / ".DS_Store").write_bytes(b"e")
    (root / ".git" / "config").write_bytes(b"f")
    (root / "node_modules" / "x" / "readme.md").write_bytes(b"g")
    return root


def _run(monkeypatch, args, responder):
    posted: list[dict] = []
    lock = threading.Lock()

    def _fake_post(url, **kw):
        with lock:
            posted.append({"name": kw["files"]["file"][0], **(kw.get("data") or {})})
        return responder(kw["files"]["file"][0])

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = MagicMock()
    client.base_url = "http://backend"
    client._auth_headers.return_value = {}
    monkeypatch.setattr(docs_mod, "_client", lambda _ctx: client)
    result = CliRunner().invoke(docs_mod.docs, ["upload", *args])
    return result, posted


def _ok(name):
    if name.endswith(".xyz"):
        return httpx.Response(400, json={"detail": "File type '.xyz' is not supported."})
    if name == "top.pdf":
        return httpx.Response(200, json={"status": "parsed", "unchanged": True})
    return httpx.Response(200, json={"status": "parsed", "blocksCount": 2})


def test_directory_needs_recursive(tmp_path, monkeypatch):
    result, posted = _run(monkeypatch, [str(_tree(tmp_path))], _ok)
    assert result.exit_code != 0
    assert "--recursive" in result.output
    assert posted == []


def test_recursive_maps_subfolders_and_skips_junk(tmp_path, monkeypatch):
    result, posted = _run(monkeypatch, ["-r", str(_tree(tmp_path))], _ok)
    assert result.exit_code == 0, result.output
    by_name = {p["name"]: p for p in posted}
    assert set(by_name) == {"top.pdf", "q1.docx", "chart.png", "data.xyz"}
    assert by_name["top.pdf"]["folder_path"] == "Reports"
    assert by_name["q1.docx"]["folder_path"] == "Reports/2026"
    assert all(p["skip_unchanged"] == "true" and "folder_id" not in p for p in posted)
    assert "2 uploaded, 1 unchanged, 1 unsupported, 0 failed" in result.output


def test_folder_option_prefixes_directory_folders(tmp_path, monkeypatch):
    result, posted = _run(monkeypatch, ["-r", str(_tree(tmp_path)), "--folder", "Archive"], _ok)
    assert result.exit_code == 0, result.output
    assert {p["folder_path"] for p in posted} == {"Archive/Reports", "Archive/Reports/2026"}


def test_failures_are_listed_and_exit_nonzero(tmp_path, monkeypatch):
    def _boom(name):
        return httpx.Response(500, text="kaboom") if name == "q1.docx" else _ok(name)

    result, _ = _run(monkeypatch, ["-r", str(_tree(tmp_path))], _boom)
    assert result.exit_code != 0
    assert "1 failed" in result.output
    assert "Reports/2026/q1.docx" in result.output


def test_reupload_flag_disables_skip(tmp_path, monkeypatch):
    _, posted = _run(monkeypatch, ["-r", "--reupload", str(_tree(tmp_path))], _ok)
    assert {p["skip_unchanged"] for p in posted} == {"false"}


def test_tailnet_mode_sends_no_token(monkeypatch):
    from aiplatform.http import AIPlatformClient

    monkeypatch.setenv("AIPLATFORM_AUTH", "tailnet")
    monkeypatch.delenv("AIPLATFORM_ID_TOKEN", raising=False)
    client = AIPlatformClient(env="local", base_url="https://studio.example.ts.net:8446/api/proxy")
    assert client._auth_headers() == {}
