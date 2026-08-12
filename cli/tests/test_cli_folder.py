"""Tests for `aitana folder` subcommands."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


@respx.mock
def test_folder_list_hits_scoped_path() -> None:
    route = respx.get(f"{BASE}/api/buckets/bkt-1/folders").mock(return_value=httpx.Response(200, json=[]))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "folder", "list", "--bucket", "bkt-1"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert route.calls.last.request.method == "GET"
    assert route.calls.last.request.url.params.get("limit") == "50"


@respx.mock
def test_folder_create_inherit_omits_access_control() -> None:
    route = respx.post(f"{BASE}/api/buckets/bkt-1/folders").mock(
        return_value=httpx.Response(201, json={"folderId": "f1"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "folder",
            "create",
            "--bucket",
            "bkt-1",
            "--path",
            "reports/2026",
            "--display-name",
            "2026 Reports",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["path"] == "reports/2026"
    assert body["displayName"] == "2026 Reports"
    assert "accessControl" not in body  # inherit by default


@respx.mock
def test_folder_create_with_explicit_private_access() -> None:
    route = respx.post(f"{BASE}/api/buckets/bkt-1/folders").mock(
        return_value=httpx.Response(201, json={"folderId": "f1"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "folder",
            "create",
            "--bucket",
            "bkt-1",
            "--path",
            "secret",
            "--display-name",
            "Secret",
            "--access-type",
            "private",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["accessControl"] == {"type": "private"}
