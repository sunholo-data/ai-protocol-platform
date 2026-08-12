"""Tests that the MCP toolset declares UI_EXTENSION_CAPABILITIES.

The MCP Apps spec asks the *client* to advertise that it can render UI
resources back from the server (mimeType ``text/html;profile=mcp-app``).
The canonical declaration is via the ClientSession ``capabilities`` arg,
e.g.::

    capabilities = {
        "extensions": {
            "io.modelcontextprotocol/ui": {
                "mimeTypes": ["text/html;profile=mcp-app"],
            }
        }
    }

ADK's ``StreamableHTTPConnectionParams`` does NOT plumb that through to the
underlying ``ClientSession.initialize()`` call (verified 2026-04-30 via
``mcp__adk-mcp__search_code`` — `MCPSessionManager._create_client` calls
``streamablehttp_client(...)`` with no capabilities arg, then SessionContext
calls ``ClientSession.initialize()`` with library defaults).

Workaround: declare UI support via the connection params ``headers`` field —
``x-aitana-mcp-ui-supported: text/html;profile=mcp-app``. Spec-compliant
servers should key off this; the live ext-apps map-server emits UI resources
unconditionally so the demo still works either way. Documented in
docs/design/v6.1.0/mcp-app-integrations.md Open Questions.

These tests lock the workaround in place so a future ADK upgrade that DOES
plumb capabilities through can swap to the canonical path without losing
the declaration.
"""

from __future__ import annotations

import pytest
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from tools.mcp.registry import (
    UI_CAPABILITY_HEADER,
    UI_CAPABILITY_MIME_TYPE,
    _build_toolset,
)


class TestUiExtensionCapabilities:
    def test_toolset_declares_ui_extension_capabilities_via_header(self):
        """HTTP toolset's connection params include the UI capability header.

        Workaround for ADK not plumbing client capabilities through. When ADK
        adds capability passthrough, this test should be amended to assert on
        ``connection_params.capabilities`` instead and the header workaround
        removed from registry.py.
        """
        config = {"url": "https://map.example.com/mcp", "transport": "http"}
        toolset = _build_toolset("ext-apps-map", config)
        assert toolset is not None
        assert isinstance(toolset, McpToolset)
        assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)
        headers = toolset._connection_params.headers or {}
        assert UI_CAPABILITY_HEADER in headers
        assert UI_CAPABILITY_MIME_TYPE in headers[UI_CAPABILITY_HEADER]

    def test_toolset_declares_ui_capability_for_sse_transport(self):
        """SSE transport must also declare the capability — same workaround."""
        config = {"url": "https://map.example.com/sse", "transport": "sse"}
        toolset = _build_toolset("ext-apps-map", config)
        assert toolset is not None
        assert isinstance(toolset._connection_params, SseConnectionParams)
        headers = toolset._connection_params.headers or {}
        assert UI_CAPABILITY_HEADER in headers

    def test_toolset_preserves_server_configured_headers(self):
        """If a server config also has its own headers (e.g. an HMAC secret),
        they must coexist with the UI capability header — neither wins on
        collision because they're under different keys."""
        config = {
            "url": "https://map.example.com/mcp",
            "transport": "http",
            "headers": {"x-server-secret": "abc"},
        }
        toolset = _build_toolset("ext-apps-map", config)
        assert toolset is not None
        headers = toolset._connection_params.headers or {}
        assert headers.get("x-server-secret") == "abc"
        assert UI_CAPABILITY_HEADER in headers

    def test_server_configured_capability_header_wins_on_collision(self):
        """If a server explicitly sets the UI capability header itself (perhaps
        because it announces a different MIME variant), the operator's choice
        wins. We don't double-write."""
        config = {
            "url": "https://map.example.com/mcp",
            "headers": {UI_CAPABILITY_HEADER: "text/html+mcp,custom/profile"},
        }
        toolset = _build_toolset("ext-apps-map", config)
        assert toolset is not None
        headers = toolset._connection_params.headers or {}
        # Operator-supplied value preserved.
        assert headers[UI_CAPABILITY_HEADER] == "text/html+mcp,custom/profile"

    def test_existing_http_transport_selection_unchanged(self):
        """Regression for the existing http/sse transport selection logic —
        adding the capability header MUST NOT alter which connection param
        class is chosen."""
        http_config = {"url": "http://localhost:9000/mcp", "transport": "http"}
        sse_config = {"url": "http://localhost:9000/sse", "transport": "sse"}
        default_config = {"url": "http://localhost:9000/mcp"}

        assert isinstance(_build_toolset("a", http_config)._connection_params, StreamableHTTPConnectionParams)
        assert isinstance(_build_toolset("a", sse_config)._connection_params, SseConnectionParams)
        # No transport field defaults to streamable HTTP.
        assert isinstance(_build_toolset("a", default_config)._connection_params, StreamableHTTPConnectionParams)


# Optional integration test against a live local map-server. Skipped in CI.
@pytest.mark.integration
def test_ui_resource_meta_present_on_live_map_server():
    """When pointed at a real ext-apps map-server, the show-map tool's
    `_meta.ui.resourceUri` should be present in tools/list responses.

    Not run in CI — requires a local map-server on :3001. Driven by the
    M3 integration step.
    """
    pytest.skip("Live integration test — run manually against localhost:3001")
