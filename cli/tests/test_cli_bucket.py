"""Tests for `aitana bucket` subcommands."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


@respx.mock
def test_bucket_list_sends_get_with_params() -> None:
    route = respx.get(f"{BASE}/api/buckets").mock(return_value=httpx.Response(200, json=[]))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "bucket", "list", "--tag", "ops", "--limit", "10"])
    assert result.exit_code == 0, result.output
    assert route.called
    req = route.calls.last.request
    assert req.method == "GET"
    assert req.url.params.get("tag") == "ops"
    assert req.url.params.get("limit") == "10"


@respx.mock
def test_bucket_show_hits_by_id() -> None:
    route = respx.get(f"{BASE}/api/buckets/bkt-1").mock(return_value=httpx.Response(200, json={"bucketId": "bkt-1"}))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "bucket", "show", "bkt-1"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert route.calls.last.request.method == "GET"


@respx.mock
def test_bucket_create_posts_expected_payload() -> None:
    route = respx.post(f"{BASE}/api/buckets").mock(return_value=httpx.Response(201, json={"bucketId": "new"}))
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "bucket",
            "create",
            "--display-name",
            "Reports",
            "--gcs-bucket",
            "aitana-reports-dev",
            "--access-type",
            "specific",
            "--email",
            "alice@example.com",
            "--email",
            "bob@example.com",
            "--bucket-tag",
            "ops",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["displayName"] == "Reports"
    assert body["gcsBucket"] == "aitana-reports-dev"
    assert body["region"] == "europe-west1"
    assert body["accessControl"] == {
        "type": "specific",
        "emails": ["alice@example.com", "bob@example.com"],
    }
    assert body["tags"] == ["ops"]


def test_bucket_create_specific_requires_email() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "bucket",
            "create",
            "--display-name",
            "X",
            "--gcs-bucket",
            "x",
            "--access-type",
            "specific",
        ],
    )
    assert result.exit_code != 0
    assert "--email" in result.output


def test_bucket_create_domain_requires_domain() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "bucket",
            "create",
            "--display-name",
            "X",
            "--gcs-bucket",
            "x",
            "--access-type",
            "domain",
        ],
    )
    assert result.exit_code != 0
    assert "--domain" in result.output


@respx.mock
def test_bucket_grant_does_get_then_put_with_merged_emails() -> None:
    existing = {
        "bucketId": "bkt-1",
        "accessControl": {"type": "specific", "emails": ["alice@example.com"]},
    }
    get_route = respx.get(f"{BASE}/api/buckets/bkt-1").mock(return_value=httpx.Response(200, json=existing))
    put_route = respx.put(f"{BASE}/api/buckets/bkt-1").mock(
        return_value=httpx.Response(200, json={"bucketId": "bkt-1"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "bucket", "grant", "bkt-1", "--email", "carol@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert get_route.called and put_route.called
    body = json.loads(put_route.calls.last.request.content)
    assert body["accessControl"]["emails"] == ["alice@example.com", "carol@example.com"]


@respx.mock
def test_bucket_revoke_removes_email() -> None:
    existing = {
        "bucketId": "bkt-1",
        "accessControl": {"type": "specific", "emails": ["alice@example.com", "bob@example.com"]},
    }
    respx.get(f"{BASE}/api/buckets/bkt-1").mock(return_value=httpx.Response(200, json=existing))
    put_route = respx.put(f"{BASE}/api/buckets/bkt-1").mock(
        return_value=httpx.Response(200, json={"bucketId": "bkt-1"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "bucket", "revoke", "bkt-1", "--email", "alice@example.com"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(put_route.calls.last.request.content)
    assert body["accessControl"]["emails"] == ["bob@example.com"]


@respx.mock
def test_bucket_grant_rejects_non_specific_acl() -> None:
    existing = {
        "bucketId": "bkt-1",
        "accessControl": {"type": "public"},
    }
    respx.get(f"{BASE}/api/buckets/bkt-1").mock(return_value=httpx.Response(200, json=existing))
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "bucket", "grant", "bkt-1", "--email", "x@y.z"],
    )
    assert result.exit_code != 0
    assert "specific" in result.output


def test_bucket_list_sends_bearer_from_env() -> None:
    """AITANA_ID_TOKEN env var is surfaced in the Authorization header."""
    with respx.mock() as mock:
        route = mock.get(f"{BASE}/api/buckets").mock(return_value=httpx.Response(200, json=[]))
        runner = CliRunner()
        result = runner.invoke(main, ["--env", "local", "bucket", "list"])
        assert result.exit_code == 0, result.output
        auth = route.calls.last.request.headers.get("authorization")
        assert auth == "Bearer test-token-123"
