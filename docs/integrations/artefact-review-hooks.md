# Artefact review hooks — fork adoption howto

**Sprint reference:** [v6.2.0 sprint 2.13 design doc](../design/v6.2.0/implemented/artefact-render-hook.md)
**For:** Forks rendering MCP-app artefacts in contexts that need policy-level content review on top of the iframe sandbox — classroom builders (AIPLA), compliance-regulated demos, customer-brand-protected deployments, kiosk-mode safety.

This howto walks a fork operator from "I want to inspect HTML before it renders in an iframe" to a registered reviewer that blocks bad content with a typed refusal panel. The platform ships the Protocol + permissive default; the actual policy is fork-side.

---

## TL;DR

**Frontend (low overhead; can be bypassed by malicious devtools):**

```typescript
// In your fork's app bootstrap (e.g. providers, _app.tsx):
import { setArtefactReviewer } from "@/components/protocols/ArtefactReviewer";

setArtefactReviewer({
  async review({ html, toolName, serverId }) {
    if (html.includes("<script>")) {
      return {
        action: "block",
        message: "Contains forbidden <script> tag.",
        reasonCode: "FORBIDDEN_TAG",
        appealUrl: "https://example.com/appeal",
      };
    }
    return { action: "approve" };
  },
});
```

**Backend (unbypassable; costs a backend round-trip):**

```python
# In your fork's startup module (after env is loaded):
from protocols.artefact_review import (
    ArtefactDecision,
    register_artefact_reviewer,
)

class MyServerSideReviewer:
    async def review(self, request):
        if "<script>" in request.html:
            return ArtefactDecision(
                action="block",
                message="Contains forbidden <script> tag.",
                reason_code="FORBIDDEN_TAG",
                appeal_url="https://example.com/appeal",
            )
        return ArtefactDecision(
            action="approve", message=None, reason_code=None, appeal_url=None,
        )

register_artefact_reviewer(MyServerSideReviewer())
```

That's the full surface. The platform's `MCPAppToolCallRouter` consults the frontend reviewer; `mcp_proxy._forward` optionally consults the backend reviewer. Both paths emit the same refusal UI.

---

## How it works

```
Tool call result (MCP App)
  │  _meta.ui.resourceUri
  ▼
mcpClient.readResource(uri) ──► /api/proxy/mcp/{server_id}
  │                                         │
  │                            (backend) optional reviewer
  │                            consulted here. block → 403
  │                            with structured body.
  ▼
fetch html + csp
  │
  ▼  (frontend) consultArtefactReviewer(html, metadata)
  │  500ms soft budget — slow reviewer = degrade to approve
  │  reviewer crash = approve + console.error in dev
  │
  ▼
decision.action
  ├─ approve → <AppRenderer> renders unchanged
  ├─ warn    → <ArtefactWarningStripe> wraps <AppRenderer>
  └─ block   → <ArtefactRefused> renders (iframe NEVER mounts)
                 + audit POST /api/proxy/api/sessions/{id}/artefact-blocked
```

The hook is **above the iframe sandbox + CSP layer** — defence-in-depth, not replacement. A reviewer that crashes or is bypassed leaves the sandbox boundary intact (the safety net). Both layers stay.

---

## Client-side vs server-side: which to pick

| Layer | Pros | Cons | Pick when |
|---|---|---|---|
| **Frontend** | Zero backend round-trip; runs in the user's browser; can inspect after the resource is loaded; easy to ship a fork-side React reviewer | Bypassable by malicious devtools; runs in the user's process | UX hints (yellow stripe with notes), simple tag bans, low-stakes deployments |
| **Backend** | Unbypassable (the user can't disable it via devtools); body inspection happens at the proxy boundary; easier audit story (all blocks go through one log point) | Adds a backend round-trip; harder to debug from devtools | Safety-relevant policy, compliance-regulated deployments, anything that must be enforced |

**Recommendation:** ship the same logic in BOTH layers if your policy is safety-relevant. The TS + Python Protocols have mirror shapes — write the policy once per language, register in both.

---

## TS ⟷ Python field mirror

The frontend and backend Protocols MUST have identical field sets so a JSON 403 body emitted by the backend round-trips cleanly to the frontend's refusal handler.

| TypeScript (frontend) | Python (backend) | Notes |
|---|---|---|
| `toolName` | `tool_name` | MCP tool that produced the artefact |
| `serverId` | `server_id` | MCP server id (`tool_configs.mcp.servers` key) |
| `resourceUri` | `resource_uri` | Artefact URI (e.g. `ui://render/abc`) |
| `html` | `html` | The rendered HTML body — the reviewer's main input |
| `csp` | `csp` | `string \| null` — frontend has the parsed CSP; backend sees raw |
| `structuredContent` | `structured_content` | Tool result payload (often used for context) |
| `invocationId` | `invocation_id` | For audit dedup |

Decision fields:

| TypeScript | Python | Notes |
|---|---|---|
| `action: "approve" \| "warn" \| "block"` | `action: Literal["approve", "warn", "block"]` | Same three values |
| `message` (on warn / block) | `message: str \| None` | User-facing prose |
| `reasonCode` (on warn / block) | `reason_code: str \| None` | Machine-readable enum; rendered in a chip |
| `appealUrl` (on block) | `appeal_url: str \| None` | Optional link to a fork-side appeal flow |

The TS Decision is a true discriminated union (narrowing on `action`). Python uses a flat dataclass with nullable fields — the JSON wire shape is identical.

---

## AIPLA-style static-analysis sketch (frontend)

AIPLA's specific ruleset is fork-side; here's a sketch of the shape:

```typescript
import { parseDocument } from "htmlparser2";

const FORBIDDEN_TAGS = new Set(["script", "iframe", "object", "embed"]);
const FORBIDDEN_HANDLERS = /^on[a-z]+$/i;
const MAX_HTML_BYTES = 64 * 1024;

setArtefactReviewer({
  async review({ html, toolName }) {
    if (html.length > MAX_HTML_BYTES) {
      return {
        action: "block",
        message: `Artefact exceeds size limit (${MAX_HTML_BYTES} bytes).`,
        reasonCode: "SIZE_LIMIT",
      };
    }
    const doc = parseDocument(html);
    const violations: string[] = [];
    walk(doc, (node) => {
      if (node.type !== "tag") return;
      if (FORBIDDEN_TAGS.has(node.name)) {
        violations.push(`tag:${node.name}`);
      }
      for (const attr of Object.keys(node.attribs ?? {})) {
        if (FORBIDDEN_HANDLERS.test(attr)) {
          violations.push(`handler:${attr}`);
        }
      }
    });
    if (/\beval\s*\(/.test(html)) violations.push("eval");
    if (/fetch\s*\(\s*["']https?:\/\//.test(html)) violations.push("external-fetch");

    if (violations.length === 0) return { action: "approve" };
    return {
      action: "block",
      message: `Artefact violates classroom policy: ${violations.join(", ")}.`,
      reasonCode: "POLICY_VIOLATION",
      appealUrl: `https://aipla.school/review/${encodeURIComponent(toolName)}`,
    };
  },
});

function walk(node: unknown, visit: (n: any) => void) {
  visit(node);
  const children = (node as { children?: unknown[] }).children;
  if (Array.isArray(children)) for (const c of children) walk(c, visit);
}
```

For Python the equivalent uses `html.parser` from the stdlib or `lxml.html`.

---

## Audit log shape

Every BLOCK fires an audit POST on the frontend side:

```
POST /api/proxy/api/sessions/{sessionId}/artefact-blocked
Content-Type: application/json

{
  "tool_name": "physics_sim_builder",
  "server_id": "demo-mcp-server",
  "reason_code": "FORBIDDEN_TAG",
  "invocation_id": "tc-abc123"
}
```

Backend BLOCKS additionally land at the proxy boundary, logged at info level:

```
mcp_proxy: server-side artefact blocked server_id=<id> reason=<reason_code>
```

The backend ALSO returns a 403 with:

```json
{
  "type": "artefact_blocked",
  "message": "Contains forbidden <script> tag.",
  "reason_code": "FORBIDDEN_TAG",
  "appeal_url": "https://example.com/appeal"
}
```

The frontend's `ArtefactRefused` mount handler renders this 403 body as the same refusal panel it would have rendered from a client-side block — UI is identical regardless of where the block came from.

---

## Performance budget

| Layer | Budget | What happens if exceeded |
|---|---|---|
| Frontend | 500ms (soft) | Promise.race fires; degrade to approve + `console.warn`; lingering reviewer Promise's eventual decision is discarded |
| Backend | 100ms (soft) | `mcp_proxy` emits a warn log `{tool_name, server_id, html_size, duration_ms}`; response NOT delayed |

Hard timeouts aren't enforced — slow reviewers don't block render. The iframe sandbox is the safety net; a slow reviewer means worse UX but never broken UX.

**If your reviewer is consistently slow:** the platform's existing OTel telemetry will surface it. Sprint 2.14 will land tenant attribution on every span so per-cohort reviewer latency is filterable.

---

## Configuration knobs

The platform doesn't ship config knobs for review behaviour — every detail lives in your reviewer's implementation. The platform's only setting is "is there a reviewer registered?".

If your reviewer needs config (e.g. tag allow-list, size limit), keep it inside your reviewer impl: read your fork's env vars at app bootstrap, capture them in the reviewer's closure.

---

## What's gated, what's not

- ✅ **MCP-app artefacts** that go through `MCPAppToolCallRouter` → `<AppRenderer>` are gated.
- ✅ **Backend `resources/read` responses** with `text/html` content are gated at the proxy when a server-side reviewer is registered.
- ❌ **A2UI specs** — inert JSON; the SDK validator + the v0.9 schema already covers them.
- ❌ **Tool result text** — only the artefact HTML is reviewed. Tool text outputs (chat content) are not subject to the artefact review hook.
- ❌ **Other `resources/read`** mime types (JSON, plain text, images) — scope guard restricts the hook to `text/html`.

---

## What the user sees

**Approve** — nothing. The artefact renders normally.

**Warn** — the artefact renders below a yellow stripe showing the reviewer's message + a reason-code chip. `role="status"` + `aria-live="polite"` — screen readers announce it but don't interrupt.

**Block** — the artefact does NOT render. A rose-bordered panel shows:
- The backend message verbatim
- The reason code in a monospace chip
- Optional "Appeal →" link (target=_blank, rel=noopener+noreferrer)
- `role="alert"` + `aria-live="assertive"` — screen readers announce it as critical

The block panel persists until the next agent turn produces a new artefact (or none).

---

## Smoke testing locally

```bash
# 1. Register a stub frontend reviewer in your fork's app bootstrap:
#    setArtefactReviewer({
#      async review({ html }) {
#        if (html.includes("<script>")) {
#          return { action: "block", message: "blocked",
#                   reasonCode: "TEST_BLOCK" };
#        }
#        return { action: "approve" };
#      }
#    });

# 2. Open an existing MCP-app demo (e.g. the Cesium map).
make dev
# Fire a turn that produces an artefact. The permissive default
# approves it; switching the snippet above to block-on-any-html
# triggers the refusal panel.

# 3. For server-side smoke: register the Python reviewer at
#    backend startup AND watch:
#    - /api/proxy/mcp/{server_id} responds 403 with structured body
#    - The frontend's ArtefactRefused renders the same panel
```

The platform's own test suite is the canonical smoke for both layers:
- Frontend: `MCPAppToolCallRouter.review.test.tsx` covers the 4 paths
- Backend: `tests/api_tests/test_mcp_proxy_artefact_review.py` covers proxy interception

---

## Migration from hand-rolled artefact filters

If your fork already inspects artefact HTML before render (a custom wrapper around `AppRenderer`, a server-side script filter, a CSP injector), migration is mechanical:

1. **Move your inspection logic into a class** implementing `ArtefactReviewer` (`async review` method only).
2. **Delete your custom wrapper / filter** — the platform's hook handles invocation.
3. **Register your reviewer** at app bootstrap (frontend) or backend startup (backend or both).
4. **Test the gate paths** against the platform's 4-path matrix as a template.

The old hand-rolled UX (custom error component? generic 500?) is replaced by `ArtefactRefused` + `ArtefactWarningStripe` automatically.

---

## Open questions / follow-ups

- **Headless-render preview**: AIPLA mentioned wanting it (Playwright in Docker rendering the artefact and inspecting the rendered DOM). Heavier ask; not in v1. The Protocol supports it — a reviewer impl can spawn a headless browser and inspect the post-script DOM before returning a decision.
- **Async + cancellable**: if a reviewer takes 30s, can the user cancel? Tie to the existing `AbortController` in `useSkillAgent.stop`. Documented but deferred.
- **Allow-list of registered reviewers**: should the platform refuse to set a reviewer not in a known-good registry? Recommendation: no — registration is a code call inside the fork's bootstrap, not a runtime config.

---

## Related docs

- [Sprint 2.13 design doc](../design/v6.2.0/implemented/artefact-render-hook.md) — full Protocol contract, axiom alignment, security considerations.
- [mcp-app-integrations](../design/v6.1.0/implemented/mcp-app-integrations.md) — the sandbox + render pipeline this extends.
- [Sprint 2.12 budget-enforcement howto](budget-enforcement.md) — sibling AIPLA-extension sprint with a similar Protocol shape.
- [AIPLA ADR-013](https://www.sunholo.com/aipla/architecture.html#adr-013-artefact-safety-content-review-pipeline) — the request that surfaced this.
