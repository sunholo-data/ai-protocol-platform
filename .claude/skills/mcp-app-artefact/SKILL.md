---
name: mcp-app-artefact
description: >
  Add or modify an MCP App artefact — a hand-curated HTML/JS/WASM surface
  rendered as a sandboxed cross-origin iframe in the workbench. Covers the
  static-artefact path (one shared mcp-sandbox Cloud Run service, files under
  infrastructure/mcp-sandbox/artefacts/<name>/v<version>/), the decision tree for
  "A2UI vs MCP-App iframe", the host chain (StaticArtefactFrame → sandbox.html
  proxy → inner iframe), CONTENT-DRIVEN iframe sizing, the two app→AI channels
  (A2UI surface-context vs MCP iframe-context), the confidential-content +
  ADR-013 security gates, and the Cloud Build wiring (NEXT_PUBLIC_MCP_SANDBOX_URL,
  ALLOWED_HOST_ORIGINS). Use when the user says "add a new MCP app", "new
  artefact", "embed an interactive visualization", "the artefact is blank/tiny",
  "size the iframe", "the artefact can't talk to the AI", or references
  mcp-sandbox / StaticArtefactFrame / the obligation artefact. Do NOT use for
  declarative tool-result UI (that's A2UI — see docs/design/v6.7.0/
  tool-results-as-a2ui.md) or agent-skills authoring (skill-creator).
license: Apache-2.0
metadata:
  author: Aitana platform (adapted from the AIPLA cphu-aipla-app fork)
  version: "0.1.0"
  added: "2026-07-13"
---

# MCP App Artefact — sandboxed HTML/JS/WASM surfaces

> **Companion skills:** [`agent-protocols`](../agent-protocols/SKILL.md) for the
> MCP / MCP Apps / A2UI spec disambiguation (read it before designing a surface);
> this skill is the **how to ship one on this platform** guide. Adapted from the
> downstream AIPLA fork's `mcp-app-artefact` skill, retargeted to platform's real
> components, paths, and the A2UI-first architecture.

## First question — do you even need an MCP-App iframe?

**Default to A2UI, not an iframe.** CLAUDE.md's protocols-first rule is
architectural: tool results render as A2UI on the workbench, user interaction
renders as A2UI in chat. An MCP-App iframe is the *heaviest* UI tier and the only
one that runs arbitrary JS. Reach for it ONLY when the surface genuinely needs
what A2UI can't express:

| Need | Use | Reference |
|---|---|---|
| Show a tool's structured result (table, cards, key-diffs) | **A2UI on the workspace** (result→A2UI mapping, generic `A2UISurfaceMount`) | `docs/design/v6.7.0/tool-results-as-a2ui.md` |
| Collect structured input back to the AI (form, picker, confirm) | **A2UI in chat** (surface-action / surface-action-run loop) | `docs/design/v6.7.0/tool-input-elicitation-a2ui.md` |
| Richer static layout than A2UI, still no JS | **generative-html surface** (sanitized, no scripts) | `docs/design/v6.7.0/generative-ui-surface.md` |
| **Live JS / WASM / canvas / pointer-driven simulation** | **MCP-App iframe** (THIS skill) | `ppa-obligation-analysis` (WASM deontic engine) |

The one live exemplar is **`ppa-obligation-analysis`** — a verified deontic
settlement engine compiled to WASM, running interactive what-if recompute in the
browser. That's the bar: if your surface isn't running real compute/canvas in the
client, it probably belongs in A2UI, not an iframe.

## The security rule comes first (read CLAUDE.md § Security Hard Rules)

Artefacts render **confidential customer content** (contracts, financials). The
whole double-iframe + separate-origin architecture exists to keep that content
inside the Aitana GCP edge. Non-negotiables:

- **No external fetch of any kind** from the artefact HTML — no `fetch()` to a
  third-party origin, no CDN `<script src>`, no `@import`, no remote fonts, no
  remote images. The inner artefact runs under a strict `ARTEFACT_CSP`
  (`default-src 'none'`, inline-only) served by `mcp-sandbox/serve.ts`; a remote
  load silently fails in prod.
- **Contract data reaches the artefact only via the host** (`ui/obligation/
  payload`-style host→app notification), fetched by the backend/host with the
  user's auth — never a public URL. A derived artefact of private content is
  never public.
- The artefact is a **pure client**: it computes on data the host handed it. It
  must not phone home.

If a design would make any derivative of private content reachable without the
auth gate, refuse it and propose the host-fetch alternative.

## File layout

```
infrastructure/mcp-sandbox/
├── artefacts/<name>/v<version>/
│   ├── index.html          # the artefact — inline CSS + JS, no external loads
│   └── assets/             # git-ignored large runtime (wasm etc.), fetched by a script
├── src/sandbox.ts          # the proxy bridge (compiled to public/sandbox.js)
├── sandbox.html            # loads sandbox.js
├── serve.ts                # serves sandbox.html + /artefacts/* under strict CSP
└── cloudbuild.yaml         # _ALLOWED_HOST_ORIGINS
```

Version directories (`v1`, `v2`) are immutable-ish: bump the version for a
breaking artefact change so cached hosts don't get a mismatched contract.

## The host chain (know it — the render bugs live here)

```
Workbench artifact tab
  → <ObligationArtefactTab>-style wrapper        (frontend/src/components/workspace/)
    → <StaticArtefactFrame>                        (host: JSON-RPC + sizing + handshake)
      → ${SANDBOX_ORIGIN}/sandbox.html             (proxy, SEPARATE origin, mcp-sandbox service)
        → inner iframe (document.write of index.html; same-origin with the proxy)
```

- **`StaticArtefactFrame.tsx`** owns the spec lifecycle: mounts `sandbox.html`,
  waits for `ui/notifications/sandbox-proxy-ready`, fetches the artefact HTML,
  sends `ui/notifications/sandbox-resource-ready {html, sandbox}`, answers the
  artefact's `ui/initialize` with `hostContext` (theme/displayMode/locale),
  responds to `ping`, forwards `ui/update-model-context` to `onUpdateModelContext`,
  and exposes `sendNotification(method, params)` via `useImperativeHandle`.
- **The wrapper** (e.g. `ObligationArtefactTab.tsx`) owns artefact-specific
  routing: pushes the payload after `onInitialized`, handles the artefact's
  events, persists state, and mirrors the "what's on screen" summary to the AI
  (see "Talk to the AI" below). Mirror this file for a new artefact.
- **`sandbox.ts`** is the relay: validates the host referrer against
  `ALLOWED_HOST_ORIGINS`, `document.write`s the HTML into the inner iframe, and
  relays JSON-RPC both ways with explicit origins. It ALSO measures the content
  (see sizing).

## CONTENT-DRIVEN iframe sizing (the recurring "blank/tiny" trap)

An `<iframe>` is a **replaced element** and this one is **cross-origin**, so the
host can neither trust `height:100%` (collapses to the 150px UA default) NOR read
the content height (`iframe.contentDocument` throws cross-origin). Three earlier
attempts failed by sizing the iframe to the *pane* — the wrong target. The fix
(shipped 2026-07-12), and it works because we own all three pieces:

1. **Artefact** renders at its natural content height (no fill hacks, no fixed
   `height:100%` on the body).
2. **`sandbox.ts`** (same-origin with the artefact) measures
   `documentElement/body scrollHeight` after `document.write` + on a
   `ResizeObserver`, and `postMessage`s `{method:"ui/notifications/size",
   params:{height}}` to the host.
3. **`StaticArtefactFrame`** listens for that method and sets
   `iframe.style.height = <reported>px` (ceil'd; a fallback holds until the first
   report). The enclosing tabpanel is `overflow-auto`, so a tall artefact scrolls.

If a new artefact renders as a strip, check that chain — never re-derive it.
See memory `mcp_apps_artefact_render_gotchas` for the other two blank-on-deployed
causes (`_MCP_SANDBOX_URL` not baked; `ALLOWED_HOST_ORIGINS` missing the origin).

## Talk to the AI — two app→AI channels (pick by surface type)

**An MCP-App artefact can feed the agent what's on screen so an analyst can ask
for comments.** Platform has TWO channels — pick by whether the artefact is an
A2UI workbench surface or a true MCP-App tool-call UI:

### A2UI surface-context (the workbench-artefact path — use this)

For an artefact mounted as an A2UI workbench surface (the obligation artefact,
and anything you build via the wrapper pattern), the surface's **data model
already reaches the agent every turn** — no gating, no backend change:

```
artefact emits ui/update-model-context {structuredContent}
  → wrapper's onUpdateModelContext mirrors a summary into the surface data model
      registry.appendMessages(surfaceId, [{updateDataModel:{value: {...view}}}])
  → readA2uiSurfaceState() snapshots dataModel.get('/') every turn   (SurfaceRegistry.tsx)
  → forwardedProps.a2ui_surface_state                                (useSkillAgent.ts)
  → wrap_with_a2ui_surface_context injects it into the prompt         (backend/adk/a2ui_surface_context.py)
```

**The artefact owns the summary.** Build an AI-legible `view` object (the RESULT
on screen — not just the inputs) and emit it; the wrapper forwards it. The
obligation artefact's `currentView()` is the template: net result, per-item
figures, active settings, and mode flags. Keep it lean and human-legible (the
agent reads it verbatim). Reference: `ObligationArtefactTab.tsx` `persistState` +
`handleUpdateModelContext`; memory `obligation_artefact_talks_to_ai`.

### MCP iframe-context (the true-MCP-server path)

For a real MCP-App tool-call UI (a tool result carrying a UI resource, rendered
by `MCPAppToolCallRouter` + `@mcp-ui/client`), the artefact POSTs
`ui/update-model-context` → `POST /api/sessions/{id}/iframe-context` →
`mcp_app_context.{serverId}.{toolName}` → `wrap_with_iframe_context`. This path
HARD-requires `serverId` in the skill's `tool_configs.mcp.servers` AND
`tool_configs.mcp.allow_context_writes` (per-server opt-in; 4 KB cap). Only use it
when the artefact is genuinely served by an MCP server — a static workbench
artefact is NOT, so use the A2UI path above instead.

### Host → artefact

`StaticArtefactFrame`'s `sendNotification(method, params)` pushes into the iframe
(the wrapper uses `ui/obligation/payload` to hand the artefact its data;
`ui/update-theme` and `ping` are generic). Convention for state pushes:
`<artefact>.set-<noun>`; for commands: `<artefact>.cmd-<verb>`. **Do not mirror**
a value the host just pushed back out as a telemetry event.

## Visual design (platform is theme-aware — NOT light-only)

Unlike the AIPLA fork (light-theme-only), platform artefacts are **theme-aware**:
define a semantic palette with `:root` (light) + `:root[data-theme="dark"]`
overrides, and apply the theme from the `ui/initialize` `hostContext.theme` +
`ui/update-theme` notifications. Copy the obligation artefact's `:root` block as
the palette baseline (`--bg/--fg/--muted/--border/--accent/--ok/--warn/--danger`
+ `-soft` tints, with dark overrides). Stress the figures the user came for — see
the obligation hero readout (`.settle-hero`). Body text ≥ 13px; keep any fixed
`min-width` under ~600px (the pane is often `md:w-1/2`) — use responsive grids.

## The A2UI-won't-render trap (verification is non-negotiable)

`docs/design` history and CLAUDE.md § 7 record this recurring bug: the agent
narrates "I updated the Workspace" but the tab stays empty because the result
never registered as a workbench artifact. **jsdom/unit tests passing does NOT
mean it renders.** An artefact change is not done until a **real browser** — or at
minimum a real AG-UI stream showing the `A2UI_SURFACE` CUSTOM event emit AND
`SurfaceRegistry` register it — confirms it. Split backend-emission from
frontend-render: stream a real run and inspect events before touching React.
See memory `a2ui_workspace_render_trap`.

## Cloud Build + deploy wiring (baked-at-build gotchas)

- **`NEXT_PUBLIC_MCP_SANDBOX_URL`** is the iframe origin — a Next.js **build-time**
  var fed from the root `cloudbuild.yaml` sub `_MCP_SANDBOX_URL`. If empty, the
  host falls back to `http://localhost:3457` → BLANK on deployed. A runtime env
  var does nothing; you must rebuild the frontend.
- **`ALLOWED_HOST_ORIGINS`** (mcp-sandbox `cloudbuild.yaml` sub
  `_ALLOWED_HOST_ORIGINS`, comma list) gates the sandbox referrer. Add every
  frontend origin (run.app URL + custom domains). `--set-env-vars` splits on
  commas → use gcloud's `^@^` custom delimiter for the multi-origin value.
- Per-env real values live in the **trigger substitutions**
  (`your-deploy-project-id`), not just the yaml defaults.
- **A push to `dev` rebuilds BOTH `mcp-sandbox` and `platform-frontend`.** An
  already-running local `make dev` builds `sandbox.ts` once at startup — restart
  it to pick up a `sandbox.ts` change.

See memory `mcp_apps_artefact_render_gotchas` and `cloudbuild_backend_file_and_
trigger_subs` for the trigger-substitution edit recipe.

## Steps to add a new artefact

1. **Decide it's really an iframe** (the table at top). If not, stop — build A2UI.
2. **Scaffold** `infrastructure/mcp-sandbox/artefacts/<name>/v1/index.html`:
   copy the obligation artefact's JSON-RPC handshake block (`rpcNotify`/
   `rpcRequest`/ping responder/`ui/initialize`/queued-until-init emit) and its
   `:root` theme palette verbatim — those are the load-bearing, spec-correct
   parts. Replace the body + compute.
3. **Content-height only** — natural layout, no fill hacks. The proxy sizes it.
4. **Emit an AI-legible `view`** on each state change (see "Talk to the AI").
5. **Host wrapper** — copy `ObligationArtefactTab.tsx`: mount `StaticArtefactFrame`,
   push the payload on `onInitialized`, mirror the `view` to the surface data
   model. Wire it into the workbench tab renderer (`ChatShell.tsx` artifact-tab
   switch).
6. **Security scan** — run the pre-ship checklist (`resources/pre-ship-checklist.md`).
   Zero external loads; strict CSP intact; ≤ size budget.
7. **Build + push** to `dev` (rebuilds mcp-sandbox + frontend). Verify DEPLOYED
   or via a real `make dev`, not jsdom.
8. **VERIFY IN A REAL BROWSER** (or a real AG-UI stream) — the render trap above.

## Reference files

| Piece | Path |
|---|---|
| Live exemplar artefact | `infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1/index.html` |
| Host frame (spec lifecycle + sizing) | `frontend/src/components/workspace/StaticArtefactFrame.tsx` |
| Wrapper exemplar (payload + AI mirror) | `frontend/src/components/workspace/ObligationArtefactTab.tsx` |
| Sandbox proxy (relay + size report) | `infrastructure/mcp-sandbox/src/sandbox.ts` |
| Sandbox server (CSP + /artefacts) | `infrastructure/mcp-sandbox/serve.ts` |
| MCP tool-call UI router | `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` |
| App→AI (A2UI surface) | `backend/adk/a2ui_surface_context.py` |
| App→AI (MCP iframe) | `backend/protocols/iframe_context_routes.py` + `backend/adk/iframe_context.py` |
| Spec disambiguation | `.claude/skills/agent-protocols/` |

## Related memory

`mcp_apps_artefact_render_gotchas` (blank/tiny causes + content-driven sizing),
`obligation_artefact_talks_to_ai` (the two app→AI channels),
`a2ui_workspace_render_trap` (the render-registration bug),
`never-silent-feedback-principle`.
