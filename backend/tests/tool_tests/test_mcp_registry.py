"""Tests for tools/mcp/registry.py and adk/tools.py MCP wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


class TestGetMcpTools:
    def test_returns_toolset_for_http_server(self):
        from tools.mcp.registry import get_mcp_tools

        config = {"url": "http://localhost:9000/mcp", "transport": "http"}
        with patch("tools.mcp.registry.get_document", return_value=config):
            result = get_mcp_tools(["my-server"])

        assert len(result) == 1
        assert isinstance(result[0], McpToolset)

    def test_returns_sse_toolset_for_sse_transport(self):
        from tools.mcp.registry import _build_toolset

        config = {"url": "http://localhost:9000/sse", "transport": "sse"}
        toolset = _build_toolset("test-server", config)
        assert isinstance(toolset, McpToolset)
        assert isinstance(toolset._connection_params, SseConnectionParams)

    def test_returns_http_toolset_for_http_transport(self):
        from tools.mcp.registry import _build_toolset

        config = {"url": "http://localhost:9000/mcp", "transport": "http"}
        toolset = _build_toolset("test-server", config)
        assert isinstance(toolset, McpToolset)
        assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)

    def test_defaults_to_http_transport(self):
        from tools.mcp.registry import _build_toolset

        config = {"url": "http://localhost:9000/mcp"}
        toolset = _build_toolset("test-server", config)
        assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)

    def test_skips_server_not_found_in_firestore(self):
        from tools.mcp.registry import get_mcp_tools

        with patch("tools.mcp.registry.get_document", return_value=None):
            result = get_mcp_tools(["missing-server"])

        assert result == []

    def test_skips_server_missing_url(self):
        from tools.mcp.registry import _build_toolset

        result = _build_toolset("bad-server", {"transport": "http"})
        assert result is None

    def test_skips_server_on_firestore_error(self):
        from tools.mcp.registry import get_mcp_tools

        with patch("tools.mcp.registry.get_document", side_effect=RuntimeError("network")):
            result = get_mcp_tools(["broken-server"])

        assert result == []

    def test_returns_multiple_toolsets(self):
        from tools.mcp.registry import get_mcp_tools

        configs = {
            "server-a": {"url": "http://a.example.com/mcp"},
            "server-b": {"url": "http://b.example.com/mcp"},
        }
        with patch("tools.mcp.registry.get_document", side_effect=lambda _, sid: configs[sid]):
            result = get_mcp_tools(["server-a", "server-b"])

        assert len(result) == 2


class TestResolveMcpTools:
    def test_empty_when_no_mcp_config(self):
        from adk.tools import resolve_mcp_tools

        result = resolve_mcp_tools({})
        assert result == []

    def test_empty_when_mcp_has_no_servers(self):
        from adk.tools import resolve_mcp_tools

        result = resolve_mcp_tools({"mcp": {}})
        assert result == []

    def test_calls_get_mcp_tools_with_status_and_returns_resolved(self):
        """G42: the agent-build path uses ``get_mcp_tools_with_status`` so it
        can fail-loud when some servers don't resolve. Happy path: all
        declared servers resolve → resolve_mcp_tools returns the list."""
        from adk.tools import resolve_mcp_tools

        fake_a, fake_b = object(), object()
        with patch(
            "tools.mcp.registry.get_mcp_tools_with_status",
            return_value=([fake_a, fake_b], []),
        ) as mock:
            result = resolve_mcp_tools({"mcp": {"servers": ["srv-1", "srv-2"]}})

        mock.assert_called_once_with(["srv-1", "srv-2"])
        assert result == [fake_a, fake_b]

    def test_g42_raises_when_some_declared_servers_dont_resolve(self):
        """G42: the durable fix — if a SKILL.md declares MCP servers that
        aren't in Firestore (typo, missed seed, wrong env), the silently-
        partial behaviour masked many bugs (incl. Friction 7's SKILL.md
        tool drift). Now `resolve_mcp_tools` raises with a diff that
        names which server_ids failed so the operator can fix the seed.
        """
        from adk.tools import McpServerResolutionError, resolve_mcp_tools

        fake_resolved = object()
        with patch(
            "tools.mcp.registry.get_mcp_tools_with_status",
            return_value=([fake_resolved], ["missing-srv"]),
        ):
            with pytest.raises(McpServerResolutionError) as ei:
                resolve_mcp_tools({"mcp": {"servers": ["resolved-srv", "missing-srv"]}})

        # Error message must list the declared count, resolved count,
        # AND the specific missing IDs so the operator can fix the seed
        # without grepping logs.
        msg = str(ei.value)
        assert "2" in msg  # declared count
        assert "1" in msg  # resolved count
        assert "missing-srv" in msg
        assert "seed_mcp_servers" in msg  # pointer at the fix

    def test_g42_raises_when_all_declared_servers_fail_to_resolve(self):
        """All-miss path: zero resolved + N missing. The previous
        behaviour silently returned []; the agent built with no MCP
        tools and looked broken at run-time. Now: clear failure at
        build time."""
        from adk.tools import McpServerResolutionError, resolve_mcp_tools

        with patch(
            "tools.mcp.registry.get_mcp_tools_with_status",
            return_value=([], ["srv-a", "srv-b"]),
        ):
            with pytest.raises(McpServerResolutionError) as ei:
                resolve_mcp_tools({"mcp": {"servers": ["srv-a", "srv-b"]}})

        assert "srv-a" in str(ei.value)
        assert "srv-b" in str(ei.value)


class TestResolveToolsErrors:
    def test_raises_on_unknown_tool(self):
        from adk.tools import resolve_tools

        with pytest.raises(ValueError, match="Unknown tool"):
            resolve_tools(["nonexistent_tool"], {})

    def test_model_aware_tools_do_not_raise(self):
        from adk.tools import resolve_tools

        # ai_search and google_search are model-aware — no ValueError
        result = resolve_tools(["ai_search", "google_search"], {})
        assert result == []

    def test_mcp_tool_does_not_raise(self):
        from adk.tools import resolve_tools

        result = resolve_tools(["mcp"], {})
        assert result == []

    def test_code_execution_does_not_raise(self):
        from adk.tools import resolve_tools

        result = resolve_tools(["code_execution"], {})
        assert result == []


class TestGetMcpToolsWithStatus:
    """G42 (template-mcp-strict-resolution.md): the new resolver API that
    surfaces missing server_ids so the agent-build path can fail-loud."""

    def test_returns_resolved_and_empty_missing_when_all_succeed(self):
        from tools.mcp.registry import get_mcp_tools_with_status

        configs = {
            "server-a": {"url": "http://a.example.com/mcp"},
            "server-b": {"url": "http://b.example.com/mcp"},
        }
        with patch(
            "tools.mcp.registry.get_document",
            side_effect=lambda _coll, sid: configs[sid],
        ):
            resolved, missing = get_mcp_tools_with_status(["server-a", "server-b"])

        assert len(resolved) == 2
        assert missing == []

    def test_tracks_server_not_in_firestore_as_missing(self):
        from tools.mcp.registry import get_mcp_tools_with_status

        with patch("tools.mcp.registry.get_document", return_value=None):
            resolved, missing = get_mcp_tools_with_status(["nonexistent"])

        assert resolved == []
        assert missing == ["nonexistent"]

    def test_tracks_server_without_url_as_missing(self):
        """Doc exists in Firestore but is malformed (no url) — must
        register as missing so the strict resolver can name it in
        the failure diff."""
        from tools.mcp.registry import get_mcp_tools_with_status

        with patch(
            "tools.mcp.registry.get_document",
            return_value={"transport": "http"},  # no url
        ):
            resolved, missing = get_mcp_tools_with_status(["url-less"])

        assert resolved == []
        assert missing == ["url-less"]

    def test_tracks_firestore_error_as_missing(self):
        """Network / IAM / Firestore unavailable: the server can't be
        resolved, so it's missing. Caller decides whether to fail-loud."""
        from tools.mcp.registry import get_mcp_tools_with_status

        with patch(
            "tools.mcp.registry.get_document",
            side_effect=RuntimeError("firestore unavailable"),
        ):
            resolved, missing = get_mcp_tools_with_status(["unreachable"])

        assert resolved == []
        assert missing == ["unreachable"]

    def test_partial_resolution_reports_both_sides(self):
        """The most common G42-triggering case: a 2-server SKILL.md
        where one server is correctly seeded and one is missing. The
        resolver must return the one that worked AND the one that
        didn't — the strict caller picks how to react."""
        from tools.mcp.registry import get_mcp_tools_with_status

        def fake_get(_coll, sid):
            if sid == "ok":
                return {"url": "http://ok.example.com/mcp"}
            return None  # missing

        with patch("tools.mcp.registry.get_document", side_effect=fake_get):
            resolved, missing = get_mcp_tools_with_status(["ok", "broken"])

        assert len(resolved) == 1
        assert missing == ["broken"]

    def test_legacy_get_mcp_tools_still_skips_silently(self):
        """Backwards-compat invariant: ``get_mcp_tools`` (the pre-G42
        API) keeps the silently-skip behaviour for admin scripts and
        test fixtures that rely on it. The fail-loud is at the
        ``resolve_mcp_tools`` agent-build layer, not here."""
        from tools.mcp.registry import get_mcp_tools

        with patch("tools.mcp.registry.get_document", return_value=None):
            result = get_mcp_tools(["missing-server"])

        assert result == []  # no exception raised


class TestDeriveInProcessMcpBaseUrl:
    """G42 part (a) (template-mcp-strict-resolution.md): the loopback URL
    a fork should seed when registering in-process MCP servers. The
    public Cloud Run hostname routes to the FRONTEND container; only
    127.0.0.1:PORT reaches THIS process's FastMCP mount. Surfaced by
    gde-ap-agent fork ("Tool 'lookup_vendor' not found" on deployed)."""

    def test_default_is_loopback_port_1956(self, monkeypatch):
        from tools.mcp.registry import derive_in_process_mcp_base_url

        monkeypatch.delenv("MCP_INTERNAL_BASE_URL", raising=False)
        monkeypatch.delenv("PORT", raising=False)

        assert derive_in_process_mcp_base_url() == "http://127.0.0.1:1956"

    def test_respects_cloud_run_PORT_env_var(self, monkeypatch):
        """Cloud Run injects ``PORT`` into the container; the helper
        must pick that up so the loopback URL targets the right bind."""
        from tools.mcp.registry import derive_in_process_mcp_base_url

        monkeypatch.delenv("MCP_INTERNAL_BASE_URL", raising=False)
        monkeypatch.setenv("PORT", "8080")

        assert derive_in_process_mcp_base_url() == "http://127.0.0.1:8080"

    def test_MCP_INTERNAL_BASE_URL_override_wins(self, monkeypatch):
        """Ops-controlled override for test fixtures / alternate binds
        / non-loopback in-process MCP scenarios."""
        from tools.mcp.registry import derive_in_process_mcp_base_url

        monkeypatch.setenv("MCP_INTERNAL_BASE_URL", "http://10.0.0.5:9000")
        monkeypatch.setenv("PORT", "1956")  # ignored — override wins

        assert derive_in_process_mcp_base_url() == "http://10.0.0.5:9000"

    def test_strips_trailing_slash_on_override(self, monkeypatch):
        """Defensive: a trailing slash in the override would produce
        ``//mcp/...`` URLs when callers concatenate. Normalize at the
        helper boundary so every caller doesn't have to remember to."""
        from tools.mcp.registry import derive_in_process_mcp_base_url

        monkeypatch.setenv("MCP_INTERNAL_BASE_URL", "http://10.0.0.5:9000/")

        assert derive_in_process_mcp_base_url() == "http://10.0.0.5:9000"

    def test_empty_override_falls_through_to_port_logic(self, monkeypatch):
        """``MCP_INTERNAL_BASE_URL=`` (set-but-empty) is treated as 'no
        override', not 'use empty string'. Cloud Run pre-declares every
        configured env var as either the set value or empty string —
        so the empty case must NOT short-circuit. The check for
        ``PUBLIC_BASE_URL`` would have been wrong here too."""
        from tools.mcp.registry import derive_in_process_mcp_base_url

        monkeypatch.setenv("MCP_INTERNAL_BASE_URL", "")
        monkeypatch.setenv("PORT", "1956")

        assert derive_in_process_mcp_base_url() == "http://127.0.0.1:1956"


class TestToolboxSidecarWiring:
    """v6.14.0 — the MCP Toolbox sidecar rides the existing registry with no new
    code. These pin the seed config + the SKILL.md wiring so a refactor can't
    silently break the loopback path."""

    def test_seeded_toolbox_config_builds_a_loopback_http_toolset(self):
        # The exact config seed_mcp_servers.py writes must resolve to a real
        # StreamableHTTP toolset pointed at the loopback sidecar.
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from seed_mcp_servers import TOOLBOX_CONFIG, TOOLBOX_URL

        from tools.mcp.registry import _build_toolset

        assert TOOLBOX_URL.startswith("http://127.0.0.1:"), "sidecar URL must be loopback"
        assert "/mcp/" in TOOLBOX_URL, "URL must be toolset-scoped (/mcp/<toolset>)"
        assert "auth" not in TOOLBOX_CONFIG, "loopback sidecar needs no auth block"

        toolset = _build_toolset("toolbox", {**TOOLBOX_CONFIG, "url": TOOLBOX_URL})
        assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)
        assert toolset._connection_params.url == TOOLBOX_URL

    def test_ppa_skill_declares_the_toolbox_server(self):
        # The one-ppa-expert SKILL.md must name toolbox under
        # toolConfigs.mcp.servers — the loud-fail (G42) contract depends on it.
        # toolConfigs is nested under `metadata:` in this skill, so find it
        # structurally rather than assuming a fixed depth.
        from pathlib import Path

        import yaml

        skill_md = Path(__file__).resolve().parents[2] / "skills" / "templates" / "one-ppa-expert" / "SKILL.md"
        if not skill_md.is_file():
            # Customer skill templates are excluded from the public template.
            # Self-skip rather than FileNotFoundError (the pattern every
            # customer-touching test here follows).
            pytest.skip("one-ppa-expert template absent (template fork)")
        # Frontmatter is the block between the first two `---` fences.
        front = skill_md.read_text().split("---", 2)[1]
        doc = yaml.safe_load(front)

        def _find_tool_configs(node):
            if isinstance(node, dict):
                if "toolConfigs" in node:
                    return node["toolConfigs"]
                for v in node.values():
                    found = _find_tool_configs(v)
                    if found is not None:
                        return found
            return None

        tool_configs = _find_tool_configs(doc) or {}
        servers = ((tool_configs.get("mcp") or {}).get("servers")) or []
        assert "toolbox" in servers, "one-ppa-expert must declare the toolbox MCP server"

    def test_resolve_mcp_tools_builds_the_ppa_skill_toolbox_server(self):
        # End-to-end through the real resolver: the skill's mcp block resolves to
        # a toolset when the server doc exists.
        from unittest.mock import patch

        from adk.tools import resolve_mcp_tools

        config = {"url": "http://127.0.0.1:5000/mcp/example", "transport": "http"}
        with patch("tools.mcp.registry.get_document", return_value=config):
            result = resolve_mcp_tools({"mcp": {"servers": ["toolbox"]}})
        assert len(result) == 1
        assert isinstance(result[0], McpToolset)

    def test_resolve_mcp_tools_fails_loud_when_sidecar_missing(self):
        # G42: a missing sidecar must raise, not silently drop the tools.
        from unittest.mock import patch

        from adk.tools import McpServerResolutionError, resolve_mcp_tools

        with patch("tools.mcp.registry.get_document", return_value=None):
            with pytest.raises(McpServerResolutionError):
                resolve_mcp_tools({"mcp": {"servers": ["toolbox"]}})


class TestHeaderSecretResolution:
    """v6.23.0 MAPS-GROUNDING — header values of the form ``${ENV_VAR}`` resolve
    from the environment so a credential never lands in a Firestore document.

    The failure path matters more than the happy path here: sending a literal
    ``${VAR}`` as a credential yields a 401 at ``tools/list``, which ADK reports
    as the indistinguishable "MCP server returned no tools" — a silent
    misconfiguration of exactly the kind G42 exists to make loud.
    """

    def test_resolves_env_reference_into_header_value(self, monkeypatch):
        from tools.mcp.registry import _build_toolset

        monkeypatch.setenv("TEST_MCP_KEY", "secret-value-123")
        config = {
            "url": "https://example.test/mcp",
            "headers": {"X-Goog-Api-Key": "${TEST_MCP_KEY}"},
        }
        toolset = _build_toolset("keyed-server", config)

        assert toolset is not None
        assert toolset._connection_params.headers["X-Goog-Api-Key"] == "secret-value-123"

    def test_plain_header_values_pass_through_untouched(self, monkeypatch):
        # Existing servers must be entirely unaffected by the new resolution step.
        from tools.mcp.registry import _build_toolset

        monkeypatch.delenv("TEST_MCP_KEY", raising=False)
        config = {
            "url": "https://example.test/mcp",
            "headers": {"X-Custom": "literal", "X-Dollar": "cost is $5"},
        }
        toolset = _build_toolset("plain-server", config)

        assert toolset is not None
        assert toolset._connection_params.headers["X-Custom"] == "literal"
        assert toolset._connection_params.headers["X-Dollar"] == "cost is $5"

    def test_unset_env_reference_fails_the_build_rather_than_sending_placeholder(self, monkeypatch):
        from tools.mcp.registry import _build_toolset

        monkeypatch.delenv("TEST_MCP_KEY", raising=False)
        config = {
            "url": "https://example.test/mcp",
            "headers": {"X-Goog-Api-Key": "${TEST_MCP_KEY}"},
        }
        assert _build_toolset("keyed-server", config) is None

    def test_empty_env_reference_also_fails(self, monkeypatch):
        # An empty string is a real deployment state (secret mounted but blank)
        # and is just as unusable as unset — it must not read as "resolved".
        from tools.mcp.registry import _build_toolset

        monkeypatch.setenv("TEST_MCP_KEY", "   ")
        config = {
            "url": "https://example.test/mcp",
            "headers": {"X-Goog-Api-Key": "${TEST_MCP_KEY}"},
        }
        assert _build_toolset("keyed-server", config) is None

    def test_unresolved_secret_makes_strict_resolution_raise(self, monkeypatch):
        # G42 contract end-to-end: an unresolvable credential is a loud failure,
        # not an agent that boots without its tools.
        from adk.tools import McpServerResolutionError, resolve_mcp_tools

        monkeypatch.delenv("MAPS_GROUNDING_API_KEY", raising=False)
        config = {
            "url": "https://mapstools.googleapis.com/mcp",
            "headers": {"X-Goog-Api-Key": "${MAPS_GROUNDING_API_KEY}"},
        }
        with patch("tools.mcp.registry.get_document", return_value=config):
            with pytest.raises(McpServerResolutionError):
                resolve_mcp_tools({"mcp": {"servers": ["maps-grounding-lite"]}})

    def test_secret_is_not_present_in_the_seeded_config(self):
        # The seed writes the NAME of the secret, never a key. If this ever
        # fails, a credential is about to be committed to git and written to
        # Firestore in plaintext.
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from seed_mcp_servers import MAPS_GROUNDING_CONFIG

        assert MAPS_GROUNDING_CONFIG["headers"]["X-Goog-Api-Key"] == "${MAPS_GROUNDING_API_KEY}"

    def test_seeded_maps_config_builds_a_streamable_http_toolset(self, monkeypatch):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from seed_mcp_servers import MAPS_GROUNDING_CONFIG, MAPS_GROUNDING_URL

        from tools.mcp.registry import _build_toolset

        monkeypatch.setenv("MAPS_GROUNDING_API_KEY", "test-key")
        toolset = _build_toolset("maps-grounding-lite", MAPS_GROUNDING_CONFIG)

        assert toolset is not None
        assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)
        assert toolset._connection_params.url == MAPS_GROUNDING_URL
        assert toolset._connection_params.headers["X-Goog-Api-Key"] == "test-key"
