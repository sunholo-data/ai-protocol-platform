"""Tests for `aiplatform user` — the person-first diagnostic group."""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from aiplatform.cli import main
from aiplatform.commands import user as user_mod
from aiplatform.commands.user import (
    _collapse_auth_lines,
    effective_roles,
    normalise_account,
    propagation_line,
)

NOW = _dt.datetime(2026, 9, 14, 9, 40, tzinfo=_dt.UTC)

# Deterministic projects: the same tests must pass against this deployment's
# real config.yaml and against the shipped example (all CI and upstream have),
# whose placeholder ids the command refuses by design.
_PROJECTS = {"dev": "test-project-dev", "test": "test-project-test", "prod": "test-project-prod"}


@pytest.fixture(autouse=True)
def _pin_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_mod, "_LOG_CFG", {"projects": dict(_PROJECTS)})
    # The propagation verdict compares the grant against the wall clock (token
    # TTL). Pin it: this suite passed for exactly one hour after it was written
    # and then went red in CI at 12:14 the same day.
    monkeypatch.setattr(user_mod, "_now", lambda: NOW)


RAW_ADA = {
    "localId": "Fx8uid",
    "email": "msc@example.com",
    "displayName": "Ada Lovelace",
    "emailVerified": True,
    "providerUserInfo": [{"providerId": "google.com"}],
    "createdAt": "1741090023000",
    "lastLoginAt": "1789377657000",  # 2026-09-14 09:20:57 UTC
    "lastRefreshAt": "2026-09-14T09:20:57.466Z",
    "validSince": "1741090023",
    "customAttributes": json.dumps({"groupTags": ["aitana-admin"]}),
}


def _fs_doc(fields: dict) -> dict:
    def enc(v):
        if isinstance(v, str):
            return {"stringValue": v}
        if isinstance(v, bool):
            return {"booleanValue": v}
        if isinstance(v, list):
            return {"arrayValue": {"values": [enc(x) for x in v]}}
        if v is None:
            return {"nullValue": None}
        return {"stringValue": str(v)}

    return {"fields": {k: enc(v) for k, v in fields.items()}}


# --- pure helpers ----------------------------------------------------------


def test_normalise_account_decodes_claims_and_timestamps() -> None:
    acct = normalise_account(RAW_ADA)
    assert acct["uid"] == "Fx8uid"
    assert acct["domain"] == "example.com"
    assert acct["claim_tags"] == ["aitana-admin"]
    assert acct["providers"] == ["google.com"]
    assert acct["last_sign_in"].year == 2026
    assert acct["last_refresh"].tzinfo is not None
    assert acct["tokens_valid_since"].year == 2025


def test_normalise_account_tolerates_missing_claims() -> None:
    acct = normalise_account({"localId": "x", "email": "a@b.c"})
    assert acct["claim_tags"] == []
    assert acct["last_sign_in"] is None


def test_effective_roles_speaks_friendly_names() -> None:
    assert effective_roles(["aitana-admin"])[0].startswith("PLATFORM ADMIN")
    assert "tenant admin of b.com" in effective_roles(["tenant-admin:b.com"])[0]
    assert "NOT the admin panel" in effective_roles(["one-admin"])[0]
    assert "admin panel: NO" in effective_roles(["example-team"])[0]
    assert "admin panel: NO" in effective_roles([])[0]


def test_propagation_grant_after_last_refresh_is_not_yet_visible() -> None:
    acct = normalise_account(RAW_ADA)
    audit = [{"action": "grant_group_tag", "ts": "2026-09-14T09:27:44+00:00", "actorEmail": "admin@x.com"}]
    line = propagation_line(acct, audit, NOW)
    assert line is not None
    assert "NOT YET IN THEIR TOKEN" in line
    assert "refresh-claims msc@example.com" in line


def test_propagation_refresh_after_grant_is_propagated() -> None:
    acct = normalise_account(RAW_ADA)
    acct["last_refresh"] = _dt.datetime(2026, 9, 14, 9, 30, tzinfo=_dt.UTC)
    audit = [{"action": "grant_group_tag", "ts": "2026-09-14T09:27:44+00:00", "actorEmail": "admin@x.com"}]
    assert "PROPAGATED" in propagation_line(acct, audit, NOW)


def test_propagation_none_without_claim_changes() -> None:
    acct = normalise_account(RAW_ADA)
    assert propagation_line(acct, [{"action": "upsert_client", "ts": "2026-09-14T09:27:44+00:00"}], NOW) is None
    assert propagation_line(acct, [], NOW) is None


def test_collapse_auth_lines_summarises_per_minute_and_keeps_route_hits() -> None:
    lines = [
        ("2026-09-14T09:20:45.1Z", "auth: authenticated uid=Fx8uid"),
        ("2026-09-14T09:20:46.1Z", "auth: authenticated uid=Fx8uid"),
        ("2026-09-14T09:20:58.3Z", '"GET /api/skills?ownerId=Fx8uid HTTP/1.1" 200'),
        ("2026-09-14T09:20:58.4Z", "auth: authenticated uid=Fx8uid"),
        ("2026-09-14T09:21:00.1Z", "auth: authenticated uid=Fx8uid"),
    ]
    out = _collapse_auth_lines(lines)
    assert out[0].startswith("2026-09-14T09:20") and "3 authenticated requests" in out[0]
    assert "GET /api/skills" in out[1]
    assert "1 authenticated request" in out[2] and "requests" not in out[2]
    assert len(out) == 3


# --- commands (HTTP + gcloud mocked) ----------------------------------------


def _mock_request(responses: dict[str, object]):
    """Route httpx calls by URL substring -> JSON body (or (status, body))."""

    def handler(method, url, **kw):
        for key, body in responses.items():
            if key in str(url):
                status, payload = body if isinstance(body, tuple) else (200, body)
                return httpx.Response(status, json=payload, request=httpx.Request(method, url))
        raise AssertionError(f"unexpected {method} {url}")

    return handler


@patch.object(user_mod, "_access_token", return_value="tok")
def test_find_filters_and_prints_only_matches_across_envs(_tok) -> None:
    other = {"localId": "o", "email": "someone@else.com", "displayName": "Someone Else"}
    handler = _mock_request({"accounts:batchGet": {"users": [RAW_ADA, other]}})
    with patch.object(user_mod.httpx, "request", side_effect=handler):
        result = CliRunner().invoke(main, ["user", "find", "lovelace"])
    assert result.exit_code == 0, result.output
    assert "msc@example.com" in result.output
    assert "someone@else.com" not in result.output  # filtered, never a dump
    assert "aitana-admin" in result.output
    # one row per env the account exists in (dev/test/prod all return the same fixture)
    assert result.output.count("msc@example.com") == 3


@patch.object(user_mod, "_access_token", return_value="tok")
def test_find_no_match_says_so(_tok) -> None:
    handler = _mock_request({"accounts:batchGet": {"users": []}})
    with patch.object(user_mod.httpx, "request", side_effect=handler):
        result = CliRunner().invoke(main, ["user", "find", "arias", "--env", "prod"])
    assert result.exit_code == 0
    assert "No account matching 'arias'" in result.output


def test_placeholder_project_is_named_as_configuration(monkeypatch) -> None:
    monkeypatch.setattr(user_mod, "_LOG_CFG", {"projects": {"prod": "your-project-id-production"}})
    result = CliRunner().invoke(main, ["--env", "prod", "user", "show", "a@b.c"])
    assert result.exit_code != 0
    assert "placeholder project" in result.output
    assert "materialize-config" in result.output


def test_show_requires_a_deployed_env() -> None:
    result = CliRunner().invoke(main, ["user", "show", "a@b.c"])
    assert result.exit_code != 0
    assert "--env dev|test|prod" in result.output


@patch.object(user_mod, "_access_token", return_value="tok")
def test_show_renders_roles_derived_tags_and_propagation(_tok) -> None:
    client_doc = _fs_doc(
        {"derived_group_tags": ["example-team"], "display_name": "Example Co", "default_skill": "demo-assistant"}
    )
    audit_rows = [
        {
            "document": _fs_doc(
                {
                    "action": "grant_group_tag",
                    "target": "msc@example.com",
                    "ts": "2026-09-14T09:27:44+00:00",
                    "actorEmail": "admin@x.com",
                }
            )
        }
    ]
    req_handler = _mock_request({"accounts:lookup": {"users": [RAW_ADA]}})
    with (
        patch.object(user_mod.httpx, "request", side_effect=req_handler),
        patch.object(
            user_mod.httpx,
            "get",
            side_effect=lambda url, **kw: httpx.Response(200, json=client_doc, request=httpx.Request("GET", url)),
        ),
        patch.object(
            user_mod.httpx,
            "post",
            side_effect=lambda url, **kw: httpx.Response(200, json=audit_rows, request=httpx.Request("POST", url)),
        ),
    ):
        result = CliRunner().invoke(main, ["--env", "prod", "user", "show", "msc@example.com"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "[ref: Fx8uid]" in out
    assert "derived tags:   example-team" in out
    assert "PLATFORM ADMIN" in out
    assert "NOT YET IN THEIR TOKEN" in out
    assert "grant_group_tag  by admin@x.com" in out


@patch.object(user_mod, "_access_token", return_value="tok")
def test_show_unknown_email_points_at_find(_tok) -> None:
    handler = _mock_request({"accounts:lookup": {}})
    with patch.object(user_mod.httpx, "request", side_effect=handler):
        result = CliRunner().invoke(main, ["--env", "test", "user", "show", "nobody@example.com"])
    assert result.exit_code != 0
    assert "never signed in there" in result.output
    assert "user find nobody" in result.output


@patch.object(user_mod, "_access_token", return_value="tok")
@patch("aiplatform.commands.logs.subprocess.run")
def test_activity_resolves_uid_then_reads_backend_logs(mock_run, _tok) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "2026-09-14T09:21:00.3Z\tauth: authenticated uid=Fx8uid\t\n"
            "2026-09-14T09:20:45.5Z\tauth: authenticated uid=Fx8uid\t\n"
        ),
        stderr="",
    )
    handler = _mock_request({"accounts:lookup": {"users": [RAW_ADA]}})
    with patch.object(user_mod.httpx, "request", side_effect=handler):
        result = CliRunner().invoke(main, ["--env", "prod", "user", "activity", "msc@example.com", "--since", "1h"])
    assert result.exit_code == 0, result.output
    log_filter = mock_run.call_args.args[0][3]
    assert 'textPayload:"Fx8uid"' in log_filter
    assert 'labels.container_name="sidecar"' in log_filter
    assert "--project=test-project-prod" in mock_run.call_args.args[0]
    assert "2 backend line(s)" in result.output
    assert "logs client --uid Fx8uid" in result.output


@patch.object(user_mod, "_access_token", return_value="tok")
@patch("aiplatform.commands.logs.subprocess.run")
def test_activity_empty_window_is_not_silent(mock_run, _tok) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    handler = _mock_request({"accounts:lookup": {"users": [RAW_ADA]}})
    with patch.object(user_mod.httpx, "request", side_effect=handler):
        result = CliRunner().invoke(main, ["--env", "dev", "user", "activity", "msc@example.com"])
    assert result.exit_code == 0
    assert "did not reach this env's backend" in result.output
