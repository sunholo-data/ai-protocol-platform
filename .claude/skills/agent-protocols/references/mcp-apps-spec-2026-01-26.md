# MCP Apps SEP-1865 — Sandboxed Iframe Protocol

**Version:** 2026-01-26  
**Status:** Implemented in platform v6.1.0

## Overview

MCP Apps are sandboxed iframes that the agent can open as interactive UI surfaces. They
communicate with the host page via `window.postMessage` using a structured envelope.

## Iframe Sandbox Requirements

```html
<iframe
  sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
  src="https://<mcp-sandbox-url>/apps/<app-id>"
/>
```

`allow-same-origin` is required for the iframe to read `window.parent.postMessage` origin.

## postMessage Envelope

All messages between iframe and host use this envelope:

```typescript
interface MCPAppMessage {
  type: string;          // message type (see below)
  payload: unknown;      // type-specific payload
  source: "mcp-app";    // identifies the sender as an MCP App iframe
}
```

## Message Types (iframe → host)

| Type | Payload | Description |
|------|---------|-------------|
| `ui/update-model-context` | `{ key: string; value: unknown }` | Push a key-value pair into ADK session state under `mcp_app_context.<key>` |
| `ui/ready` | `{}` | Iframe signals it has loaded and is ready to receive messages |

## Message Types (host → iframe)

| Type | Payload | Description |
|------|---------|-------------|
| `host/tool-input` | `{ toolInput: unknown }` | Send the tool call's input JSON to the iframe |
| `host/session-context` | `{ sessionId: string; skillId: string }` | Session metadata on mount |

## CSP Requirements

The MCP sandbox server must serve:
```
Content-Security-Policy: frame-ancestors 'self' https://<platform-domain>;
```

The platform page must not set `frame-ancestors` in a way that blocks the sandbox origin.

## Session State Integration

When the iframe calls `ui/update-model-context`, the platform backend writes the value
into the ADK session under `mcp_app_context.<key>`. The agent reads this via:

```python
context_value = tool_context.state.get("mcp_app_context.my_key")
```

The `wrap_with_iframe_context` function in `backend/protocols/iframe_context_routes.py`
prepends the full `mcp_app_context` namespace into the agent's system prompt so the
agent is always aware of current iframe state.

## Implementation Notes

- `_MCP_SANDBOX_URL` in `cloudbuild.yaml` is the only config required. Empty = disabled.
- `useSandboxedIframeMessages` hook in the frontend handles the `postMessage` listener.
- Session bootstrap must complete before the first iframe push — otherwise the
  `ui/update-model-context` call will 404 on a missing ChatSessionIndex.
