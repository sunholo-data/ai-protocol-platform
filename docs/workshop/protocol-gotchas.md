# Protocol gotchas — the bear traps we found, so you don't have to

> The new agent UI protocols (AG-UI, A2UI, MCP Apps) are young. The specs
> are clear on paper; the SDKs are at v0.x. Most things just work. A few
> don't — and those few cost real days of debugging when you hit them
> blind. This page captures every protocol-layer trap we hit during the
> v6 bring-up so you can route around them.

Each entry links to the workshop block where the trap most often
appears, plus a one-line fix and the design doc / commit that proves
the fix works.

## How to use this page

- **Reading the workshop agenda?** Glance at the "block N" column to
  see which traps lie ahead.
- **Building your own skill in block 5?** Search Cmd-F for the
  framework you're using (AG-UI, A2UI, MCP, CopilotKit).
- **Hit something weird?** Check here before opening DevTools — odds
  are it's catalogued.

---

## AG-UI traps (block 2)

### 1. Wire `state` is **one turn behind**

**Symptom:** Frontend sends `forwardedProps.document_ids = [A, B]` after
the user opens doc B. Backend reads `state.document_ids = [A]` from the
prior turn's `STATE_SNAPSHOT` and ignores the fresh signal. Agent denies
doc B exists.

**Why:** `HttpAgent.prepareRunAgentInput` puts `state: this.state` in
the body, where `this.state` is updated **by `STATE_SNAPSHOT` events
the backend emits**. That makes the wire `state` channel a
*backend-output mirror*, round-tripped one turn late. Perfect for
resumption hints, lethal for "what did the user just do."

**Fix:** Per-turn signals belong on `forwardedProps`. Parser priority:
`forwardedProps > top-level body > state`. The bare-key state write
keeps round-tripping; the parser just refuses to read the round-trip.

**Sub-trap:** Don't reach for the `temp:` prefix to make the round-trip
"go away." ADK's `temp:` semantics are in-invocation only —
`base_session_service._trim_temp_delta_state` strips temp keys from
`event.actions.state_delta` *before* persistence; `ag_ui_adk` then
re-fetches the session, so the temp value lives only on a transient
copy that gets garbage-collected. By the time your callback runs,
`temp:document_ids` is gone.

### 2. Own the **full** AG-UI boundary

**Symptom:** You "adopted AG-UI" — you use `ag_ui_adk` on the backend.
But your HTTP endpoint takes a custom `{message: str}` body. Every call
from `HttpAgent` results in a `RUN_ERROR` in the frontend — no stack
trace, no 4xx, just a 200 OK with an error event in the SSE stream.

**Why:** `HttpAgent` sends the AG-UI wire format:
`{messages: [...], threadId, state, forwardedProps}`. Pydantic
*silently drops extra fields* — your custom `{message: str}` body sees
`message = ""`, ADK errors on empty input, the error gets serialised as
a streaming `RUN_ERROR` event, which the frontend treats as a normal
agent failure.

**Fix:** Accept `RunAgentInput` at the HTTP layer; let `ag_ui_adk`
consume it directly. Half-adopting a protocol breaks silently —
*half-adoption is worse than no adoption* because the failure mode is
invisible.

### 3. **CopilotKit ≠ AG-UI** (don't reach for CopilotKit by reflex)

**Symptom:** You install `@copilotkit/react-ui` to get the chat UI.
Nothing wired correctly. Your `<CopilotKit>` provider needs a
`runtimeUrl`, but you've got a plain AG-UI backend at `/api/proxy/...`.

**Why:** `@copilotkit/react-*` speaks CopilotKit's *own* GraphQL
runtime, not AG-UI SSE. Internally CopilotKit's runtime uses AG-UI to
talk to its backend adapters — but the React side is its own thing.

**Fix:** Use `@ag-ui/client.HttpAgent` directly. v6 has 0 lines of
CopilotKit code; the entire chat UI is `agent.subscribe({...})` in
[`useSkillAgent.ts`](useSkillAgent.ts) (see [`code-tour.md`](code-tour.md)).

If you want the polished CopilotKit chrome (sidebar, popup, themes),
you can still do that — but adopt the full CopilotKit stack
end-to-end (runtime + adapters), don't try to half-marry it to a
bare-AG-UI backend.

### 4. Tool-only assistant turns can render blank

**Symptom:** You emit a tool call that returns A2UI JSON. The workspace
pane updates, but the chat bubble that hosted the tool-call indicator
is empty — no spinner, no "the agent called X" badge, nothing.

**Why:** `useSkillAgent.toSkillMessage` was treating
`content: undefined` (which is what assistant messages with only tool
calls have) as "no message," and skipping the render entirely. The
dispatcher attached to that bubble never mounted, so the surface
update arrived with no host to mount in.

**Fix:** Coerce `content: undefined` to `""` for assistant messages.
The bubble renders even when empty — it just becomes a host for tool
indicators and inline UI.

---

## A2UI traps (block 3)

### 5. The SDK wrapper synthesizes v0.8 messages internally

**Symptom:** Your skill emits perfectly spec-compliant v0.9 A2UI
specs (the LLM has the v0.9 schema from `render_as_llm_instructions()`),
your backend validates against v0.9, but the workspace pane stays empty.
No errors in the console. The renderer just silently drops the spec.

**Why:** `@a2ui/react`'s default entry point is the v0.8 wrapper —
`A2UIViewer({root, components, data})` synthesizes v0.8
`{beginRendering, surfaceUpdate, dataModelUpdate}` messages internally.
When the spec is v0.9 shape (`{version, createSurface, updateComponents,
updateDataModel}`), `isStaticSpec` returns false, the wrapper's
dispatcher silently drops the message.

**Fix:** Import from the version-pinned entry — `@a2ui/react/v0_9` +
`@a2ui/web_core/v0_9` — and use `MessageProcessor` + `<A2uiSurface>`
directly. v6 has one `MessageProcessor` per surface, auto-`createSurface`
to handle LLM message-ordering drift, idempotent on tool-call id.

### 6. v0.9 renamed "Standard" → **"Basic"**

**Symptom:** Your skill prompt asks the LLM to "use the Standard
catalog." LLM emits valid v0.9, but the components fail validation.

**Why:** Alpha-API churn. v0.9 renamed the canonical catalog. Backend
constant is now `BasicCatalog`; `CatalogConfig.from_path()` for custom
catalogs.

**Fix:** Update skill prompts. Better: the SDK auto-injects the schema
via `render_as_llm_instructions()` — don't hand-write the catalog name
into prompts in the first place.

---

## MCP Apps traps (block 4)

### 7. MCP Apps is UI-by-**REFERENCE**, not embedded

**Symptom:** Your router inspects the `tools/call` result looking for
an embedded UI resource. It's not there.

**Why:** The MCP Apps spec is UI-by-reference:
- `tools/list` declares `_meta.ui.resourceUri = "ui://server/widget.html"`
  on each UI-bearing tool.
- `tools/call` returns ONLY data (plain text + `_meta.viewUUID`); NO
  embedded UI resource.
- The host **separately** calls `resources/read(uri)` to fetch the
  HTML.

**Fix:** Renderer needs **both** the tool definition (with
`_meta.ui.resourceUri`) AND either an MCP `Client` (to fetch the
resource) or pre-fetched `html`. `@mcp-ui/client@7.0.0`'s `AppRenderer`
makes this explicit; older mental models from blog posts are stale.

### 8. `@mcp-ui/client@7.0.0` is **`AppRenderer`** (not `UIResourceRenderer`)

v7 rename. Older blog posts and docs reference `UIResourceRenderer`.
Use `AppRenderer`. Same component, different name.

### 9. **Two** iframe→host RPC channels, not one

**Symptom:** Cesium map demo shows Munich correctly when the agent
calls `show-map`. User asks "what city is currently centred?" — agent
calls `show-map` again instead of answering from context. The agent has
no idea what's on screen.

**Why:** MCP Apps defines **two** distinct iframe→host RPC methods:
- `ui/message` — synthetic chat turns ("I clicked Munich" → goes into
  the next agent turn as if the user said it)
- `ui/update-model-context` — structured state (current map bounds,
  selected city) merged into the agent's NEXT-turn context under
  `mcp_app_context.{server}.{tool}`

Without an `onUpdateModelContext` handler, the iframe gets
`MCP error -32601: No handler for method`, the map still renders, but
the agent stays blind to its own UI.

**Fix:** Wire both. In `@mcp-ui/client@7.0.0`, `ui/update-model-context`
surfaces via the catch-all `onFallbackRequest` callback (dispatch on
`request.method`), not a dedicated prop. Backend needs a parallel
endpoint (v6: `POST /api/sessions/{id}/iframe-context`) that merges the
update into session state for the next turn. **Seven access gates** on
that endpoint — see [`code-tour.md`](code-tour.md) #7.

### 10. The sandbox **must** be a separate origin

**Symptom:** Inner iframe with `allow-same-origin` reads host cookies
+ Firebase tokens.

**Why:** That's how same-origin works. The sandbox iframe is
defence-in-depth; it has to be on a different origin from the host
shell or `allow-same-origin` defeats the whole point.

**Fix:** v6 ships an
[`infrastructure/mcp-sandbox/`](https://github.com/sunholo-data/ai-protocol-platform/tree/main/infrastructure/mcp-sandbox) — tiny Express server on port 3457
in dev, separate Cloud Run service in deployed envs. CSP is set via HTTP
headers (tamper-proof; meta-tag CSP can be modified by served HTML).
Referrer + origin validation on every postMessage. Sprint 2.13's
artefact-review hook (see [`code-tour.md`](code-tour.md) #7) sits
*above* this — defence-in-depth, not replacement.

### 11. MCP TS SDK opens GET + POST + DELETE on streamable_http

**Symptom:** You set up a backend proxy that only handles POST for the
MCP server. Client aborts every call with `net::ERR_ABORTED`. No useful
error message.

**Why:** `StreamableHTTPClientTransport` opens a long-lived GET (the
SSE channel) alongside POSTs (requests). The SDK aborts the whole
session if the GET 404s. DELETE is used for session teardown.

**Fix:** Backend proxy declares GET + POST + DELETE handlers, all gated
on the same auth + allowlist. v6's [`mcp_proxy.py`](https://github.com/sunholo-data/ai-protocol-platform/blob/main/backend/protocols/mcp_proxy.py)
([`code-tour.md`](code-tour.md) #7) does this — read the route
declarations at the top.

### 12. The iframe→host bridge is host-specific — ChatGPT uses `window.openai`, not `ui/*`

**Symptom:** Your widget works in-app and in Claude Desktop / MCP
Inspector — the agent sees slider drags, clicks, selections. Load the
*same* widget into **ChatGPT** (dev-mode connector): it **renders but
goes dark**. The model has no idea what the user did in it. (A deployed
sim widget hit exactly this — displayed fine, broadcast nothing.)

**Why:** Traps #7–9 describe the **SEP-1865 postMessage bridge**
(`ui/message`, `ui/update-model-context`) — but that's what
**`@mcp-ui/client`, Claude, and Inspector** speak. **ChatGPT does not
consume those `ui/*` postMessages at all** — OpenAI's Apps SDK injects a
**`window.openai`** object instead. A widget that only posts to
`window.parent` is shouting into a void there. *(Verified live
2026-07-08.)* Microsoft 365 Copilot is the lenient one: it injects
`window.openai` **and** honours the postMessage bridge. Same split on
the tool→UI metadata: `_meta.ui.resourceUri` (standard) vs
`_meta["openai/outputTemplate"]` (ChatGPT) — **set both**.

**Fix:** Make the widget **dual-bridge** — feature-detect `window.openai`
at call time, else fall back to `ui/*` postMessage. Guard every
`window.openai.*` call (unsupported APIs are `undefined`):

| Intent | SEP-1865 (`@mcp-ui`, Claude, Inspector) | ChatGPT / Copilot |
|---|---|---|
| push state to the model | `ui/update-model-context` | `window.openai.setWidgetState` |
| surface a chat turn | `ui/message` | `window.openai.sendFollowUpMessage` |
| **mutate + get the result back** | `app.callServerTool` | `window.openai.callTool` (tool needs `openai/widgetAccessible`) |

The last row is the **mutation round-trip** — the widget calls a data
tool, the server mutates and returns, and the result lands in the widget
*and* the model's context. Confirmed in ChatGPT as plain
`window.openai.callTool` (no `mcp.*` namespace), 2026-07-08.

**Testing external hosts:** expose the local server with
`cloudflared tunnel --url http://localhost:PORT` and add `<url>/mcp` as a
ChatGPT dev-mode connector. Two sub-traps: the `*.trycloudflare.com` URL
is **ephemeral** (re-rolls on restart / laptop sleep → re-import), and
hosts **cache `tools/list` at connect** — a newly added tool needs a
connector **refresh**, not a reload.

---

## ADK + general traps (block 5)

### 13. Don't wrap your own Python as an MCP server

**Symptom:** You're tempted to expose `summarise_url(url)` as an MCP
server. Now you have a separate process, a separate transport, and
JSON-RPC overhead — for code you wrote yourself, that you could call
directly.

**Why:** MCP is for *external* tools (third-party APIs, vendor
integrations, things the agent shouldn't need source for). ADK
`FunctionTool` runs in-process — no transport, no serialisation, sub-ms
overhead.

**Fix:**
- **Internal Python you wrote?** Wrap as `FunctionTool`.
- **Third-party API you call via HTTP?** Wrap as `FunctionTool`.
- **Genuinely external service that already speaks MCP?** Use `McpToolset`
  (or `tool_configs.mcp.servers` in your `SkillConfig`).

### 14. Next.js `/api/proxy/[...path]` strips the prefix silently

**Symptom:** Your backend declares a route at `prefix="/api/proxy/mcp"`.
You curl it from the laptop — works. You navigate in-browser through
the Next.js frontend — 404.

**Why:** Next.js's catch-all at `/api/proxy/[...path]` strips
`/api/proxy/` and forwards `[...path]` to the backend. So
`/api/proxy/mcp/X` → Next strips → backend sees `/mcp/X`. Your declared
route never matches.

**Fix:** Backend prefix is just `/mcp` (no `/api/proxy/`). The curl-
from-laptop test that "worked" was misleading — it bypassed Next.js.
Test through the same path the browser uses.

---

## Where these came from

- [`docs/talks/ai-ui-protocol-stack.md`](https://github.com/sunholo-data/ai-protocol-platform/blob/main/docs/talks/ai-ui-protocol-stack.md) — the living
  verification log + anti-patterns list. Every entry here is dated and
  cites the commit / smoke test that proved the fix.
- The platform's [`docs/design/v6.X.Y/implemented/`](https://github.com/sunholo-data/ai-protocol-platform/tree/main/docs/design) sprint docs — each fix
  has a design doc with the worked example.

The talk doc is the **source of truth**. This page is the workshop
distillation — protocol traps only, no IaC/deploy gotchas, mapped to
agenda blocks.

## After the workshop

If you hit a new trap building your own skill, send it back — open a
PR against `docs/talks/ai-ui-protocol-stack.md` in the platform repo
adding a row to the verification log. The talk's credibility (and this
page's) comes from being field notes, not theory.
