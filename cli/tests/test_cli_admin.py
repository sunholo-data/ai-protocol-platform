"""Tests for `aiplatform admin whoami` — the admin role probe (v6.16.0 M7)."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


def _run() -> object:
    return CliRunner().invoke(main, ["--env", "local", "admin", "whoami"])


@respx.mock
def test_whoami_platform() -> None:
    respx.get(f"{BASE}/api/admin/whoami").mock(
        return_value=httpx.Response(200, json={"scope": "platform", "domains": [], "email": "owner@yourcompany.com"})
    )
    result = _run()
    assert result.exit_code == 0, result.output
    assert "platform" in result.output
    assert "owner@yourcompany.com" in result.output


@respx.mock
def test_whoami_tenant_lists_domains() -> None:
    respx.get(f"{BASE}/api/admin/whoami").mock(
        return_value=httpx.Response(
            200, json={"scope": "tenant", "domains": ["a.com", "b.com"], "email": "ops@a.com"}
        )
    )
    result = _run()
    assert result.exit_code == 0, result.output
    assert "tenant" in result.output
    assert "a.com" in result.output and "b.com" in result.output


@respx.mock
def test_whoami_none_explains_what_to_do() -> None:
    """A bare 'none' would leave the operator guessing between the two causes
    that actually produce it — no tag in THIS env, or a stale token."""
    respx.get(f"{BASE}/api/admin/whoami").mock(
        return_value=httpx.Response(200, json={"scope": "none", "domains": [], "email": "u@x.com"})
    )
    result = _run()
    assert result.exit_code == 0, result.output
    assert "none" in result.output
    assert "sign out" in result.output.lower()
    assert "environment" in result.output.lower()


@respx.mock
def test_whoami_tolerates_a_sparse_response() -> None:
    respx.get(f"{BASE}/api/admin/whoami").mock(return_value=httpx.Response(200, json={}))
    result = _run()
    assert result.exit_code == 0, result.output
    assert "none" in result.output
