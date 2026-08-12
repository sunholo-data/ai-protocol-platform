# ADR-013 — MCP Apps iframe Sandbox Profile

**Date:** 2026-01-26  
**Status:** Accepted  
**Deciders:** Mark (Aitana Labs)

## Context

MCP Apps are rendered as sandboxed iframes. The sandbox attribute controls the iframe's
security capabilities. Two profiles are relevant:

| Profile | `sandbox` attribute | Origin behavior |
|---------|---------------------|-----------------|
| **Scripts-only** | `allow-scripts` | Opaque origin (`"null"`) on iframe side |
| **Scripts + same-origin** | `allow-scripts allow-same-origin` | Real origin; iframe can read host cookies |

## Decision

MCP App iframes use `allow-scripts allow-forms allow-same-origin allow-popups`.

The `allow-same-origin` flag is required for:
- The iframe to perform cross-frame postMessage origin checks correctly
- The MCP sandbox-proxy pattern (`ui/initialize` + JSON-RPC bridge)
- Authenticated reads within the iframe's own same-origin context

The MCP sandbox service (`_MCP_SANDBOX_URL`) runs on a **separate origin** from the
platform frontend. This means `allow-same-origin` does NOT grant the iframe access to
the host page's cookies or localStorage — the iframe and host are on different origins.
The flag only allows the iframe to treat itself as same-origin with respect to the
sandbox service's origin.

## Consequences

**Benefits:**
- Enables the full MCP Apps spec (sandbox-proxy pattern, JSON-RPC bridge)
- Enables `AppRenderer` from `@mcp-ui/client` to function correctly
- Enables `event.origin` checking in postMessage handlers

**Risks:**
- If the sandbox service and the platform frontend are accidentally hosted on the same
  origin, `allow-same-origin` would give the iframe access to host cookies. This must
  never happen — always deploy the sandbox on a distinct origin.

## Static artefacts: use `StaticArtefactFrame`, not a raw iframe

> **Original wording 2026-05-21 (template-mcp-apps-artefacts sprint item #28)**
> recommended a `useSandboxedIframeMessages` hook with window-identity auth as the
> path for non-agent-summoned (button-click) artefacts. **Revised 2026-06-05** after
> AIPLA fork sprint MCPAPP-SPEC validated the spec-compliant path end-to-end and
> deleted the hook on merge — single-path discipline, no fallback. The
> opaque-origin sub-case below is preserved as a cautionary note so the next
> implementer doesn't rediscover it.

For non-agent-summoned artefacts (e.g. a user-facing artefact that mounts on
button click, not via a tool call), **use `StaticArtefactFrame`** from
`frontend/src/components/workspace/StaticArtefactFrame.tsx`. It mounts the
artefact inside the mcp-sandbox service's same-origin context (the spec's
sandbox-proxy pattern, §Sandbox proxy lines 470–487), which gives it a real
origin and lets you authenticate by canonical `event.origin === sandboxOrigin`
matching. The component handles the `ui/initialize` handshake, JSON-RPC 2.0
envelope parsing, `ui/update-model-context` forwarding, and the `ping`
responder per spec.

### The path NOT to take: raw `sandbox="allow-scripts"` with window-identity auth

If you reach for a raw iframe with `sandbox="allow-scripts"` (no
`allow-same-origin`) to avoid this ADR's same-origin risks:

1. `allow-scripts` without `allow-same-origin` produces an **opaque origin** on
   the iframe's side — all `postMessage` events arrive at the host with
   `event.origin === "null"`.

2. Checking `event.origin !== "null"` (or against any real origin) will silently
   reject ALL messages from the iframe. This is the most common cause of "my
   iframe sends nothing" bugs.

3. Checking `event.origin === "null"` is dangerously permissive — any
   cross-origin frame can set its origin to `"null"` via the `sandbox`
   attribute.

The only auth for this raw configuration is window-object identity
(`event.source === iframeRef.current?.contentWindow`) — which IS safe from
spoofing but is **off-spec** at the iframe ↔ host layer. AIPLA shipped that
exact pattern (a `useSandboxedIframeMessages` hook) on 2026-05-21 morning,
proved the spec-compliant alternative same day, and **deleted the hook** on
merge. The platform never adopted the off-spec path either.

**If a future use case genuinely cannot go through the sandbox proxy** (no
mcp-sandbox available, browser extension contexts, etc.), the window-identity
pattern is still the only secure auth for `allow-scripts` alone — but write a
new ADR documenting why the spec path was unavailable; don't bring the deleted
hook back.

## Alternatives Considered

**`sandbox=""` (maximum restriction):** Prevents all script execution — not viable for
interactive MCP Apps.

**No sandbox attribute:** Iframes inherit the full parent document context — unacceptable
security posture for user-supplied or external content.
