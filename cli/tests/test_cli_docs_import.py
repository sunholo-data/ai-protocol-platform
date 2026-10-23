"""Tests for `aiplatform docs import-from-bucket` — DOC-IMPORT-REF M4."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
_IMPORT_URL = f"{BASE}/api/documents/import-by-reference"


@respx.mock
def test_import_from_bucket_posts_expected_body() -> None:
    route = respx.post(_IMPORT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "docId": "doc-123",
                "status": "parsed",
                "originalFilename": "example-A.pdf",
                "blocksCount": 42,
                "storagePath": "PPAs/longform/example-A.pdf",
                "folderId": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "docs",
            "import-from-bucket",
            "--bucket",
            "example-customer-documents",
            "--object",
            "PPAs/longform/example-A.pdf",
            "--skill",
            "demo-ppa-expert",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "bucket": "example-customer-documents",
        "object": "PPAs/longform/example-A.pdf",
        "skillId": "demo-ppa-expert",
    }
    assert "parsed" in result.output
    assert "doc-123" in result.output
