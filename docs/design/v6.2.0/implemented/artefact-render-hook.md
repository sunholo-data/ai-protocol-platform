# Artefact render hook — pluggable content review for MCP-app artefacts

**Status**: Proposed
**Priority**: P2 (AIPLA need by week 6 ≈ early July; broadly useful but smaller blast radius than 2.11/2.12)
**Estimated**: ~0.5 day (Protocol + plug point in router + permissive default + tests + docs)
**Scope**: Frontend hook in `MCPAppToolCallRouter` + small backend Protocol for forks that want server-side review. No new endpoint.
**Dependencies**: Existing MCP-app rendering (sprint 1.7 + 1.25). The sandbox proxy already enforces origin + CSP boundaries; this adds a CONTENT review layer ABOVE that.
**Surfaced by**: AIPLA fork [ADR-013 — artefact safety / content review pipeline](https://www.sunholo.com/aipla/architecture.html#adr-013-artefact-safety-content-review-pipeline).
**Created**: 2026-05-19

---

## Problem Statement

The platform already enforces **iframe sandboxing** for MCP-app artefacts: separate-origin sandbox proxy, CSP headers (tamper-proof), referrer + origin validation on every postMessage. That stops cross-origin attacks, code injection into the host, network exfiltration via the iframe's own origin.

What it does NOT do is **content review**: inspecting the artefact's HTML/JS for policy-level concerns BEFORE the iframe loads. For most platform consumers, sandboxing is enough. But classroom + regulated-industry forks have additional rules:

| Consumer | Additional content rules |
|---|---|
| AIPLA (classroom) | No `eval`, no `fetch` to external URLs, no inline event handlers, tag allow-list, size limit, headless render preview before student sees it. |
| Internal compliance demos | No external font/image fetches (data egress concern). |
| Customer demos | Brand-mark check (must contain logo, must not contain competitor names). |
| Workshop kiosks | No persistence (localStorage / IndexedDB writes). |

These rules are POLICY: they vary by deployment. What's universal is the **plug point** — a hook in the artefact render pipeline where consumers can run their checks and reject / warn / approve before the iframe materialises.

AIPLA's intent ("build as an `artefact-render` MCP server in the AIPLA fork") works, but the PLATFORM needs to expose where to plug in. Today there isn't a seam.

### Current state

- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx`: receives MCP tool-call results with `_meta.ui.resourceUri`, fetches the resource via the backend MCP proxy, renders `<AppRenderer html={...} sandbox={...}>` directly.
- The HTML body flows straight from fetched resource → iframe `src`. No interception point.
- Backend `mcp_proxy` fetches the resource and forwards bytes; no inspection.
- `infrastructure/mcp-sandbox`: enforces CSP at the HTTP layer — this is the "wide net" (everyone gets the same headers), not the "fine net" (per-artefact rules).

### Impact

- **AIPLA can't ship `physics-sim-builder`** without a review pipeline; teachers won't approve student-generated artefacts being rendered without review.
- **Forks reinvent**: each one forks `MCPAppToolCallRouter` to bolt in their checks, drifting from upstream.
- **Sandbox alone is insufficient marketing claim**: "we sandbox iframes" is true but doesn't answer "what about the JS inside the iframe being malicious to the user". The hook lets forks make stronger claims.

---

## Goals

**Primary Goal:** Define an `ArtefactReviewer` Protocol (frontend) + optional server-side variant (backend) the platform calls before rendering any MCP-app artefact. Ship a permissive default impl that approves everything (current behaviour). Forks plug their own reviewer to enforce stricter policy.

**Success Metrics:**
- An MCP-app artefact arrives via tool result; the router consults the registered reviewer with the HTML body + metadata; reviewer returns `approve`, `warn`, or `block`.
- Default impl: approves everything. Existing demo (Cesium map) renders unchanged.
- AIPLA's fork: registers a `StaticAnalysisArtefactReviewer` that parses the HTML, rejects on `eval` / external `fetch` / inline handlers / unknown tags. The platform doesn't ship this reviewer — AIPLA owns the policy.
- `warn` mode: artefact renders but with a yellow-bordered "Review pending" wrapper + audit log entry.
- `block` mode: artefact does NOT render; user sees a clean refusal message + reason + an admin-only "appeal" link (fork wires the appeal endpoint).
- Performance: review budget ≤100ms for any default-shipped impl; >500ms degrades to approve + emits an error log (consumers tune their own impls).

**Non-Goals:**
- Shipping AIPLA's specific ruleset. Their rules go in their fork.
- Replacing the sandbox proxy. The hook is ABOVE the sandbox — it inspects, then the sandbox runs. Both layers stay.
- Reviewing A2UI specs (they're inert JSON; the SDK validator + our v0.9 schema already covers them).
- Server-side mandatory review. The Protocol is offered both client- and server-side; forks pick where their reviewer runs. Server-side is recommended for high-stakes deployments but not enforced.
- Headless-render preview. AIPLA mentioned wanting this; it's a heavier infrastructure ask (browser-in-Docker). Out of scope for v1; the hook supports it as a future variant.

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral if reviewer is fast (≤100ms budget); could become a latency hit if a fork ships an expensive impl. |
| 2 | EARNED TRUST | +1 | Teachers / parents can point at a policy file: "here's what we accept". Stronger trust story than "we sandbox iframes". |
| 3 | SKILLS, NOT FEATURES | 0 | Neutral — orthogonal to skills. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model implications. |
| 5 | GRACEFUL DEGRADATION | +1 | Block path returns a clean refusal with reason; warn path still renders. Reviewer crash → falls back to approve + error log (fail open, since the sandbox is still in place). |
| 6 | PROTOCOL OVER CUSTOM | 0 | Internal Protocol; no published spec. |
| 7 | API FIRST | +1 | One Protocol, one config field (`tool_configs.mcp.artefact_reviewer = "<name>"`). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every consult logs `{tool_name, server_id, html_size, decision, reason}`. Reviewers contribute to the same audit log as MCP App calls. |
| 9 | SECURE BY CONSTRUCTION | +1 | Adds an axis of defense ABOVE the existing sandbox. Defence-in-depth is the textbook security argument. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Neutral — the Protocol is symmetric across client and server. |
| | **Net Score** | **+5** | Threshold: >= +4 ✓ |

---

## Standards Compliance Check

- **Frontend Protocol** — TypeScript `interface` with async methods. No new dependency.
- **Backend Protocol** — Python `typing.Protocol`. Mirrors the frontend shape so forks can implement the same logic in either layer.
- **No new wire format** — server-side reviewers run inside the existing `/api/proxy/mcp/{server_id}` path; they intercept the response body before forwarding. Client-side reviewers run inline in `MCPAppToolCallRouter`.

---

## Design

### Overview

```
┌──────────────────────────────────────────────────────────────────┐
│ ARTEFACT RENDER FLOW                                             │
│                                                                  │
│  Tool call result (MCP App)                                      │
│     │  _meta.ui.resourceUri                                      │
│     ▼                                                            │
│  mcpClient.readResource(uri)                                     │
│     │  ← server-side reviewer optionally runs HERE               │
│     ▼  (intercepts the bytes in /api/proxy/mcp/*)                │
│  fetch html + csp                                                │
│     │                                                            │
│     ▼  client-side reviewer runs HERE                            │
│  ArtefactReviewer.review({html, metadata, sourceTool, ...})      │
│     │                                                            │
│  ┌──┴──────────────┐                                             │
│  ▼                 ▼                                             │
│ approve         block                                            │
│  │                 │                                             │
│  ▼                 ▼                                             │
│ <AppRenderer>   <ArtefactRefused message=... />                  │
│                  + audit log + onChatMessage notification        │
└──────────────────────────────────────────────────────────────────┘
```

### The Protocol

```typescript
// frontend/src/components/protocols/ArtefactReviewer.ts

export interface ArtefactReview {
  toolName: string;            // the MCP tool that produced this
  serverId: string;            // the MCP server id
  resourceUri: string;
  html: string;                // the rendered HTML body
  csp: string | null;          // the resource's _meta.ui.csp
  structuredContent: unknown;  // the tool result's structured payload
  invocationId: string;        // for idempotency
}

export type ArtefactDecision =
  | { action: "approve" }
  | { action: "warn"; message: string; reasonCode: string }
  | { action: "block"; message: string; reasonCode: string; appealUrl?: string };

export interface ArtefactReviewer {
  review(input: ArtefactReview): Promise<ArtefactDecision>;
}
```

Server-side mirror in `backend/protocols/artefact_review.py`:
```python
class ArtefactReviewer(Protocol):
    async def review(self, input: ArtefactReview) -> ArtefactDecision: ...
```

### Frontend Changes

**1. New module** `frontend/src/components/protocols/ArtefactReviewer.ts` (~40 LOC).

Types + a registry function `setArtefactReviewer(impl)` + a default impl `PermissiveArtefactReviewer` (returns `{action: "approve"}` always).

**2. `MCPAppToolCallRouter` consult** (~30 LOC patch):

After resolving the resource HTML, before constructing `<AppRenderer>`:
```typescript
const reviewer = getArtefactReviewer();
const decision = await reviewer.review({...});
if (decision.action === "block") {
  return <ArtefactRefused decision={decision} />;
}
return (
  <>
    {decision.action === "warn" && <ArtefactWarningStripe message={decision.message} />}
    <AppRenderer html={html} sandbox={...} ... />
  </>
);
```

**3. `ArtefactRefused` component** (~50 LOC):
- Renders the block message + reason code.
- Optional appeal link if the decision carries one.
- onMount fires an audit-log POST (uses the same `surface-action` endpoint pattern under a new `artefact_blocked` event name).

**4. `ArtefactWarningStripe`** (~30 LOC): a yellow-bordered wrapper component for the warn case.

### Backend Changes

**1. New module** `backend/protocols/artefact_review.py` (~80 LOC).

Protocol + types + registry. Same shape as frontend.

**2. Optional server-side interception in `mcp_proxy`** (~40 LOC patch):

When a `readResource` response is being forwarded, consult the registered reviewer first. If `block`, return a structured 403 with the decision; the frontend `MCPAppToolCallRouter` translates this into the same refusal UI it would have rendered from a client-side reviewer.

This is OPTIONAL — forks pick where they run. Recommended: server-side for high-stakes (the user can't bypass it via DevTools), client-side for low-overhead (no backend round-trip per artefact).

**3. Skill config extension**:
`tool_configs.mcp.artefact_reviewer: string | null` — points to a registered reviewer name. Default `null` (use the global default — permissive).

---

## Implementation Plan

### Phase 1 — Protocol + permissive default + tests (~0.15d)

- Frontend Protocol + types + registry.
- `PermissiveArtefactReviewer` (returns approve).
- Vitest cases: registry get/set, default-when-unregistered, async review path.

### Phase 2 — Router consult + refusal UI (~0.2d)

- `MCPAppToolCallRouter` patch.
- `ArtefactRefused` + `ArtefactWarningStripe` components.
- Vitest: block path renders refusal; warn path renders stripe + artefact; approve path renders artefact unchanged; reviewer crash falls back to approve (defence-in-depth).

### Phase 3 — Backend Protocol + optional proxy interception (~0.15d)

- Python Protocol + registry.
- `mcp_proxy.py` patch: consult server-side reviewer (when registered) before forwarding resource bytes.
- Pytest: server-side block returns structured 403; frontend handles 403 as refusal.

### Phase 4 — Docs + AIPLA reference sketch (~0.1d, can run concurrent with 1–3)

- Howto `docs/integrations/artefact-review-hooks.md`: how to plug a reviewer + the AIPLA-style sketch (static analysis with `htmlparser2`, fetch/eval/inline-handler bans, tag allow-list).
- Talk-doc audit row.

---

## Migration & Rollout

- **Backward compatible**: default impl approves everything; existing MCP-app demos (Cesium map) render unchanged.
- **No backend dependency**: pure-frontend reviewers work standalone.
- **AIPLA path**: AIPLA writes their reviewer in the fork (frontend or backend, their choice). Single `setArtefactReviewer(...)` call at app bootstrap.

---

## Testing Strategy

### Frontend (Vitest)

- Registry: set + get; default when not set.
- Router: approve → renders AppRenderer; warn → renders stripe + AppRenderer; block → renders ArtefactRefused; reviewer crash → falls back to approve + dev-only console.error.
- ArtefactRefused: renders message + reason code + optional appeal link; mount fires audit POST.

### Backend (pytest)

- Protocol conformance for the reference no-op impl.
- mcp_proxy with server-side reviewer: approve → 200 with bytes; block → 403 with structured decision.
- mcp_proxy with no server-side reviewer registered: bytes pass through unchanged (back-compat).

### Manual

- Plug a stub reviewer that blocks on `<script>` tags; run the Cesium demo; see refusal.

---

## Security Considerations

- **Defence in depth**: this hook is ON TOP of the existing sandbox. If the reviewer is bypassed or crashes, the sandbox still constrains the artefact. Fail-open is the right default for the reviewer crash case.
- **Reviewer source of truth**: server-side reviewers cannot be bypassed by a malicious frontend. Client-side reviewers can be. Document this trade-off; recommend server-side for any policy that's safety-relevant (not just UX).
- **Audit log integrity**: every decision logged with `{tool_name, server_id, html_size, decision, reason, invocation_id}`. The block path's audit log is the only record of what was refused.
- **No HTML escape into instruction stream**: the refusal message is rendered as TEXT in the chat bubble; reasonCode is a known enum. Avoid passing raw HTML from the rejected artefact into the chat surface.
- **Performance budget enforcement**: a reviewer that takes >500ms is logged at error level. Forks responsible for their own perf.

---

## Open Questions

1. **Headless-render preview?** AIPLA mentioned it. Heavier ask (Playwright in Docker). Punt to v2; the hook supports it as one impl shape.
2. **Async + cancellable?** If a reviewer takes 30s, can the user cancel? Tie to the existing `AbortController` plumbing in `useSkillAgent.stop`. Document but defer the wire-up.
3. **Allow-list of registered reviewers?** Should the platform refuse to set a reviewer that's not in a known-good registry? Recommend: no — the registration is a code call inside the fork's bootstrap, not a runtime config. Trust the fork's deployment.

---

## Related Documents

- [AIPLA ADR-013](https://www.sunholo.com/aipla/architecture.html#adr-013-artefact-safety-content-review-pipeline) — the request.
- [mcp-app-integrations](../../v6.1.0/implemented/mcp-app-integrations.md) — the sandbox + render pipeline this extends.
- [mcp-app-update-model-context.md](../../v6.1.0/implemented/mcp-app-update-model-context.md) — sibling MCP-app feature, similar gate pattern (allow_context_writes opt-in).
