# Template MCP App Artefact Quality

**Status**: ✅ G35 shipped in platform (Sprint MCP-ARTEFACT-QUALITY, 2026-06-05) — **template sync pending**. G36 N/A in platform (template has no demo artefacts to seed).
**Priority**: P2 (UX-polish for artefacts; the underlying iframe-host protocol works)
**Estimated**: 1.5d planned; ~1h actual (G36 scope-cut as N/A; shared theme.css premature with only one artefact)
**Scope**: `infrastructure/mcp-sandbox/artefacts/_template/v1/index.html` + frontend `StaticArtefactFrame` + iframe-guide
**Dependencies**: [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) (#28/#29/#30 — iframe-host protocol path); pairs with [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) G31 (Workbench mount preservation)
**Created**: 2026-06-05
**Last Updated**: 2026-06-05
**Source items**: G35 (artefacts ignore `hostContext.theme`), G36 (artefacts ship with empty/skeletal default state) — captured by gde-ap-agent fork 2026-06-03 during AP demo polish

## Implementation Status (Sprint MCP-ARTEFACT-QUALITY, 2026-06-05)

| Item | Status | Files |
|------|--------|-------|
| G35 `:root[data-theme="dark"]` palette in starter artefact | ✅ Shipped | [_template/v1/index.html](../../../infrastructure/mcp-sandbox/artefacts/_template/v1/index.html) — light + dark palettes via CSS custom properties, mirrored token-for-token; hardcoded `background: white` swapped for `var(--panel-bg)` |
| G35 artefact consumes `hostContext.theme` on init | ✅ Shipped | `_template` reads `result.hostContext.theme` from `ui/initialize` response and calls `applyTheme()` → sets `document.documentElement.dataset.theme` |
| G35 artefact listens for `ui/update-theme` runtime notifications | ✅ Shipped | `_template` registers `rpcOnNotification("ui/update-theme", …)` handler that re-applies `data-theme` |
| G35 `StaticArtefactFrame` emits `ui/update-theme` on prop change | ✅ Shipped (4 new tests, 14 total in StaticArtefactFrame.test.tsx) | [StaticArtefactFrame.tsx](../../../frontend/src/components/workspace/StaticArtefactFrame.tsx) — `initializedRef` + `lastEmittedThemeRef` + post-mount `useEffect` that emits only when post-init AND theme actually changed. No redundant emits across same-theme re-renders. |
| G35 iframe-guide theming section | ✅ Shipped | [docs/ops/mcp-apps-iframe-guide.md](../../ops/mcp-apps-iframe-guide.md) — new "Theming: honour hostContext.theme (G35)" section between "Creating a new artefact" and the existing artefact-creation content |
| G35 shared `theme.css` for cross-artefact reuse | ⏳ Deferred | Premature in platform (only one artefact, `_template`). Promote to `infrastructure/mcp-sandbox/artefacts/shared/theme.css` once a second artefact exists in the template and the duplication is real |
| G36 pre-seeded demo state | ❌ NOT APPLICABLE | Platform's `_template` is intentionally a starter scaffold, not a demo. Forks that copy it fill in their own content. The G36 friction in gde-ap-agent applied to its specific demo artefacts (ap-vendor-kg, vendor-globe, ap-dashboard) — not template-level. |

**Validation:** `npm run quality:check` — 555/555 vitest (+4 net-new this sprint), ESLint clean, tsc clean, build clean.

## Problem Statement

The MCP App spec's `ui/initialize` handshake provides a `hostContext` block — theme,
locale, sizing hints — that lets sandboxed artefacts render coherently with the host
page. The template's wiring sends `hostContext.theme` correctly; the starter artefacts
**ignore it** and hardcode colours. Separate but related: the same starter artefacts
ship with empty placeholder states ("Run the validator to populate the graph") that
make a one-glance impression of "nothing here" before the runtime data arrives. Both
defaults undercut the MCP App pitch ("rich interactive artifact running in a
sandboxed iframe") at the moment a judge or attendee opens the iframe for the first
time.

**Current State:**

- **G35** `StaticArtefactFrame` correctly sends `hostContext.theme` per MCP Apps spec (§Host Context). Artefact HTMLs under [`infrastructure/mcp-sandbox/artefacts/`](../../../infrastructure/mcp-sandbox/artefacts/) hardcode `#0a0f1e` / `#e8a800` and never read the field. Host flips to light mode → artefacts stay dark → visual clash.
- **G36** Starter artefacts (e.g. `ap-vendor-kg/index.html`) ship with `"Run the validator to populate the graph"` as the default UI. First-time judges see nothing interesting; the validator's runtime push *does* land but only after the user takes action — by then the impression "the iframe is empty" has set in.

**Impact:**

- Themed forks (anyone re-branding away from the template's defaults) get artefact UIs that visibly clash with the host shell — the "embedded vs. pasted-on" tell.
- The MCP App protocol's central pitch — that an interactive artifact lives in a same-origin sandbox iframe — is hollowed out the moment the iframe opens to an empty state. Judges in the AP competition reported "opens it once, sees nothing interesting, never comes back."

## Goals

**Primary Goal:** Starter MCP App artefacts should be **theme-current and demonstrable** the moment the iframe opens — consume `hostContext.theme`, render with realistic seed data, and update live when the agent pushes new state.

**Success Metrics:**
- All starter artefacts respond to host theme via `hostContext.theme` on init + a runtime `ui/update-theme` notification.
- Every starter artefact opens to a realistic seeded snapshot (no "waiting" placeholder); runtime pushes overlay/replace the seed.
- A shared `theme.css` palette ships once and is `@import`-ed by every artefact, so re-branding the host re-themes the artefacts without per-artefact edits.

**Non-Goals:**
- Changing the MCP Apps host-side protocol surface. `StaticArtefactFrame` already
  sends `hostContext.theme`; this doc updates the consumers, not the producer.
- Building a full theming framework for artefacts. CSS custom properties on `:root[data-theme=…]` is the entire mechanism.
- Replacing static artefacts with dynamic ones. The agent-summoned path is unchanged; this is about the starter HTMLs' content/style defaults.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Seeded artefact content + correct theming on first paint = no "empty iframe" delay |
| 2 | EARNED TRUST | +1 | Visual cohesion (artefact matches host theme) reads as "this is one product," not "two pasted-together apps" |
| 3 | SKILLS, NOT FEATURES | +1 | Skills with embedded artefacts now look polished out of the box |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing change |
| 5 | GRACEFUL DEGRADATION | +1 | Seed data renders even when the agent never pushes; theme falls back to a safe default if `hostContext` is missing |
| 6 | PROTOCOL OVER CUSTOM | +1 | Consumes `hostContext.theme` per MCP Apps spec instead of hardcoding — uses the protocol primitive correctly |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | 0 | No observability change |
| 9 | SECURE BY CONSTRUCTION | 0 | No security change; the sandbox profile is unchanged |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Artefacts are clients of the host's protocol |
| | **Net Score** | **+5** | Acceptable — proceed |

**Conflict Justifications:**
- None.

## Design

### Overview

Two changes per starter artefact: (G35) replace hardcoded colours with CSS custom
properties on `:root[data-theme]`, read `hostContext.theme` from `ui/initialize` and
react to `ui/update-theme` notifications; (G36) pre-seed each artefact with realistic
demo data lifted from the same fixtures the backend reads. Ship the theme boilerplate
once at [`infrastructure/mcp-sandbox/artefacts/shared/theme.css`](../../../infrastructure/mcp-sandbox/artefacts/shared/theme.css) so every artefact gets it via `@import` rather than copy-paste.

### G35 — `hostContext.theme` consumption + shared theme.css

**New file:** [`infrastructure/mcp-sandbox/artefacts/shared/theme.css`](../../../infrastructure/mcp-sandbox/artefacts/shared/theme.css)

```css
:root {
  /* Default to light-mode tokens; override in [data-theme="dark"] */
  --bg-base: #ffffff;
  --bg-elevated: #f7f8fb;
  --fg-primary: #0a0f1e;
  --fg-muted: #5a6172;
  --accent: #2c5fd9;
  --accent-soft: #e6edff;
  --border: #d8dde6;
}

:root[data-theme="dark"] {
  --bg-base: #0a0f1e;
  --bg-elevated: #131a2e;
  --fg-primary: #ffffff;
  --fg-muted: #9aa3b8;
  --accent: #e8a800;
  --accent-soft: rgba(232, 168, 0, 0.15);
  --border: #1f2640;
}

/* Artefact-default rules so a one-line @import lights up theming */
body { background: var(--bg-base); color: var(--fg-primary); margin: 0; }
a, .accent { color: var(--accent); }
```

**Per-artefact boilerplate** (ships in each of `ap-vendor-kg/index.html`,
`vendor-globe/index.html`, `ap-dashboard/index.html`):

```html
<link rel="stylesheet" href="../shared/theme.css">
<script type="module">
  // Reply to host's ui/initialize with our handshake response,
  // then apply the theme.
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const msg = event.data;

    if (msg?.method === "ui/initialize") {
      const theme = msg.params?.hostContext?.theme ?? "light";
      document.documentElement.dataset.theme = theme;
      window.parent.postMessage({
        jsonrpc: "2.0", id: msg.id,
        result: { ok: true, supports: ["ui/update-theme"] }
      }, "*");
      return;
    }

    if (msg?.method === "ui/update-theme") {
      document.documentElement.dataset.theme = msg.params?.theme ?? "light";
      return;
    }
  });
</script>
```

**Host-side update:** `StaticArtefactFrame` ships a `ui/update-theme` notification
whenever the host theme flips at runtime (e.g. user toggles dark mode mid-session).
The artefact's listener updates `data-theme` and CSS variables cascade.

### G36 — Pre-seeded artefact default state

**File:** [`infrastructure/mcp-sandbox/artefacts/ap-vendor-kg/index.html`](../../../infrastructure/mcp-sandbox/artefacts/ap-vendor-kg/index.html) (canonical example; sibling artefacts follow the same pattern)

Lift seed data from the same vendor-master fixture the backend reads at
`backend/tools/vendor_master/fixtures/vendors.json` (or wherever it lives). For the AP
demo:

- Acme GmbH vendor node with `V-1042` master id
- One PO node (`PO-2026-0189`) linked to Acme
- Two prior invoice nodes linked to the PO
- Canonical citations in a side panel

Mark the seed source clearly on screen:

```html
<div class="seed-watermark">
  <small>Demo data · live updates overlay this seed</small>
</div>
```

**Runtime push behaviour:** validator's `ui/update-data` payload **overlays** the seed
(per-path patches per [template-protocol-defaults.md G25](./template-protocol-defaults.md#g25-—-a2ui-updatedatamodel-per-path-patches-as-the-default)). The seed survives until the
agent has demonstrably overridden the relevant fields.

**Apply same pattern to sibling artefacts:**
- `vendor-globe/index.html` — pre-seeded with 3-4 vendor locations from fixtures
- `ap-dashboard/index.html` — pre-seeded with a snapshot of historical invoice counts

### Workshop alignment

[docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) talking
points 8 (MCP App artefacts as templates too) absorb both items as worked examples:
"never hardcode colours in an MCP App — consume `hostContext`" + "starter artefacts
need demonstrable content".

### CLI Surface

No new commands. Artefact deploys ride on the existing
[`scripts/deploy-mcp-sandbox.sh`](../../../scripts/deploy-mcp-sandbox.sh) path (to be
auto-triggered per the Friction 14 add to
[template-cloudbuild-hardening.md](./template-cloudbuild-hardening.md)).

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Write `infrastructure/mcp-sandbox/artefacts/shared/theme.css` with light + dark token palettes (G35 shared CSS) | 1.5h |
| 2 | Update `ap-vendor-kg/index.html` to `@import` theme.css + consume `hostContext.theme` + react to `ui/update-theme` (G35 canonical artefact) | 1h |
| 3 | Replicate the artefact-side boilerplate to `vendor-globe/index.html` and `ap-dashboard/index.html` (G35 siblings) | 2h |
| 4 | Update host-side `StaticArtefactFrame` to emit `ui/update-theme` notifications on host theme flip (G35 host side) | 1h |
| 5 | Pre-seed `ap-vendor-kg/index.html` with vendor-master snapshot lifted from backend fixtures; render watermark; verify overlay behaviour (G36 canonical) | 2h |
| 6 | Pre-seed `vendor-globe/index.html` + `ap-dashboard/index.html` with realistic snapshots from same fixtures (G36 siblings) | 2h |
| 7 | Visual smoke via [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md): open each artefact in light + dark host themes; assert correct colour palette + seed data visible (chrome-devtools MCP) | 1.5h |
| 8 | Docs: extend [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) iframe-guide section with theming + seeding best practices; cross-link this doc | 1h |
| 9 | Workshop talking-point 8 added to [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) | 1h |

**Total: ~13h ≈ 1.5d** (matches sprint estimate).

## Testing Strategy

- **No unit tests** for the artefact HTMLs themselves (static HTML/JS in iframes is poorly served by unit tests). Manual visual smoke is the right gate.
- **Visual smoke** via chrome-devtools MCP (operating manual: [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md)):
  1. Open the chat page with host theme = light; open each artefact; assert CSS variables resolve to light tokens (no hardcoded `#0a0f1e` visible).
  2. Flip host to dark mode; assert artefacts re-render with dark tokens within one animation frame.
  3. Open each artefact before running any agent; assert seed data is visible (the demo Acme GmbH KG snapshot is the canonical check).
  4. Run a pipeline; assert runtime pushes overlay the seed (vendor master ID still visible, status field updates).
- **Optional `StaticArtefactFrame.test.tsx`** — assert that flipping the parent's theme prop sends a `ui/update-theme` `postMessage` to the iframe (mock contentWindow).

## Success Criteria

- [ ] `infrastructure/mcp-sandbox/artefacts/shared/theme.css` exists; light + dark palettes defined as CSS custom properties (G35).
- [ ] All three starter artefacts (`ap-vendor-kg`, `vendor-globe`, `ap-dashboard`) `@import` `shared/theme.css` and consume `hostContext.theme` (G35).
- [ ] Flipping the host theme mid-session re-themes the artefacts via `ui/update-theme` notification, without re-handshake (G35).
- [ ] Each starter artefact opens to a realistic seeded snapshot (no "waiting" placeholder); a 30s glance reads as "interactive demo" not "empty iframe" (G36).
- [ ] Runtime push from the agent overlays the seed; the seed survives until the agent explicitly overrides the relevant fields (G36).
- [ ] [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) workshop talking-point 8 added.
- [ ] [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) iframe-guide section extended with theming + seeding best practices.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — items G35, G36 registered here
- [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) — iframe-host protocol (sibling); this doc consumes the `hostContext` channel that doc surfaces
- [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) — G31 Workbench tab persistence guarantees artefact handshake state survives tab switches; without that, runtime `ui/update-theme` is moot
- [template-cloudbuild-hardening.md](./template-cloudbuild-hardening.md) — Friction 14 add: artefact-path-watching Cloud Build trigger so a fork's retheme actually lands on the deployed sandbox
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — workshop tracker; talking-point 8 absorbs G35–G36
