"""Tests for `aiplatform client` subcommands."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


@respx.mock
def test_client_list_renders_table() -> None:
    payload = [
        {"domain": "acme-energy.example", "display_name": "Acme Energy", "documents_bucket": "one-docs"},
        {"domain": "acme.com", "display_name": "", "documents_bucket": None},
    ]
    respx.get(f"{BASE}/api/admin/clients").mock(return_value=httpx.Response(200, json=payload))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "client", "list"])
    assert result.exit_code == 0, result.output
    assert "acme-energy.example" in result.output
    assert "one-docs" in result.output
    assert "acme.com" in result.output


@respx.mock
def test_client_get_prints_json() -> None:
    payload = {"domain": "acme-energy.example", "display_name": "Acme Energy", "documents_bucket": "one-docs"}
    respx.get(f"{BASE}/api/admin/clients/acme-energy.example").mock(return_value=httpx.Response(200, json=payload))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "client", "get", "acme-energy.example"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["domain"] == "acme-energy.example"
    assert body["documents_bucket"] == "one-docs"


@respx.mock
def test_client_set_calls_put_with_correct_payload() -> None:
    response_payload = {
        "domain": "acme-energy.example",
        "display_name": "Acme Energy",
        "documents_bucket": "one-docs",
    }
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=response_payload)
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "client",
            "set",
            "acme-energy.example",
            "--documents-bucket",
            "one-docs",
            "--display-name",
            "Acme Energy",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["documents_bucket"] == "one-docs"
    assert body["display_name"] == "Acme Energy"


@respx.mock
def test_client_set_with_enabled_skills_sends_list() -> None:
    """`--enabled-skills a,b,c` → PUT body has enabled_skills as a 3-slug list.

    Covers the v6.4.0 ONE-DEMO M1 tenant skill filter wiring.
    """
    response_payload = {
        "domain": "acme-energy.example",
        "display_name": "Acme Energy",
        "documents_bucket": "multivac-acme-energy-bucket",
        "enabled_skills": ["one-ppa-expert", "one-doc-compare", "general-assistant"],
    }
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=response_payload)
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "client",
            "set",
            "acme-energy.example",
            "--documents-bucket",
            "multivac-acme-energy-bucket",
            "--display-name",
            "Acme Energy",
            "--enabled-skills",
            "one-ppa-expert,one-doc-compare,general-assistant",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["enabled_skills"] == [
        "one-ppa-expert",
        "one-doc-compare",
        "general-assistant",
    ]
    assert body["documents_bucket"] == "multivac-acme-energy-bucket"


@respx.mock
def test_client_set_with_empty_enabled_skills_clears_filter() -> None:
    """`--enabled-skills ""` sends enabled_skills=null → "all skills visible"."""
    response_payload = {
        "domain": "acme-energy.example",
        "display_name": "Acme Energy",
        "enabled_skills": None,
    }
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=response_payload)
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "client",
            "set",
            "acme-energy.example",
            "--enabled-skills",
            "",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["enabled_skills"] is None


@respx.mock
def test_client_set_with_derived_group_tags_sends_list() -> None:
    """`--derived-group-tags ONE,beta` → PUT body has derived_group_tags as a 2-tag list."""
    response_payload = {
        "domain": "acme-energy.example",
        "display_name": "",
        "derived_group_tags": ["ONE", "beta"],
    }
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=response_payload)
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "client",
            "set",
            "acme-energy.example",
            "--derived-group-tags",
            "ONE,beta",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["derived_group_tags"] == ["ONE", "beta"]


@respx.mock
def test_client_set_with_empty_derived_group_tags_clears() -> None:
    """`--derived-group-tags ""` sends derived_group_tags=null."""
    response_payload = {"domain": "acme-energy.example", "display_name": "", "derived_group_tags": None}
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=response_payload)
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "client", "set", "acme-energy.example", "--derived-group-tags", ""],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["derived_group_tags"] is None


@respx.mock
def test_client_delete_with_yes_skips_prompt() -> None:
    payload = {"domain": "acme-energy.example", "display_name": "Acme Energy", "documents_bucket": "one-docs"}
    route = respx.delete(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json=payload)
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "client", "delete", "acme-energy.example", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_client_delete_prompts_and_aborts_on_n() -> None:
    route = respx.delete(f"{BASE}/api/admin/clients/acme-energy.example").mock(return_value=httpx.Response(200, json={}))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "client", "delete", "acme-energy.example"], input="n\n")
    assert result.exit_code == 1  # click.confirm(abort=True) raises Abort → exit 1
    assert not route.called
    assert "Aborted" in result.output or "abort" in result.output.lower()


@respx.mock
def test_client_set_with_default_skill_sends_it_and_omits_unset_fields() -> None:
    """`--default-skill X` (v6.5.0 AUTH-LANDING) sends default_skill and, with no
    other flags, sends ONLY that — a partial upsert that won't null sibling
    fields like enabled_skills / derived_group_tags server-side."""
    route = respx.put(f"{BASE}/api/admin/clients/acme-energy.example").mock(
        return_value=httpx.Response(200, json={"domain": "acme-energy.example", "default_skill": "one-ppa-expert"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "client", "set", "acme-energy.example", "--default-skill", "one-ppa-expert"],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["default_skill"] == "one-ppa-expert"
    # Partial update: nothing else sent, so the server merge can't clobber them.
    assert "enabled_skills" not in body
    assert "derived_group_tags" not in body
    assert "display_name" not in body
