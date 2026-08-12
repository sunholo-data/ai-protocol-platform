"""Tests for `aitana groups` subcommands — wired to the v6.9.0 admin API (9.3).

Group tags are per-user claims keyed by EMAIL against /api/admin/users/{email}.
"""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
EMAIL = "alice@example.com"


@respx.mock
def test_groups_grant_posts_tag() -> None:
    route = respx.post(f"{BASE}/api/admin/users/{EMAIL}/groups").mock(
        return_value=httpx.Response(200, json={"email": EMAIL, "group_tags": ["ops"]})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "groups", "grant", "--email", EMAIL, "--tag", "ops"])
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body == {"tag": "ops"}


@respx.mock
def test_groups_revoke_deletes() -> None:
    route = respx.delete(f"{BASE}/api/admin/users/{EMAIL}/groups/ops").mock(
        return_value=httpx.Response(200, json={"email": EMAIL, "group_tags": []})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "groups", "revoke", "--email", EMAIL, "--tag", "ops"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert route.calls.last.request.method == "DELETE"


@respx.mock
def test_groups_show_gets_user() -> None:
    route = respx.get(f"{BASE}/api/admin/users/{EMAIL}").mock(
        return_value=httpx.Response(200, json={"email": EMAIL, "group_tags": ["ops", "aitana-admin"]})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "groups", "show", "--email", EMAIL])
    assert result.exit_code == 0, result.output
    assert route.called
    assert route.calls.last.request.method == "GET"
    assert "ops" in result.output
