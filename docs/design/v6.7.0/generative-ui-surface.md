# Generative UI Surface (AI-authored rich UI, sanitized — no JS)

**Status**: Proposed — design-ahead, gated on a real consumer (the SVG experiment
maturing into a demand for interactive/laid-out generated UI). Not scheduled.
**Priority**: P2 (Low)
**Estimated**: ~2.5d (frontend sanitize+mount branch + design-system injection + streaming preview + tests)
**Scope**: Fullstack (mostly frontend; small backend mapping/marker helper)
**Dependencies**: [tool-results-as-a2ui.md](tool-results-as-a2ui.md) (the result→UI emission path this reuses), [SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx) (the DOMPurify sanitize pattern this generalises), [mcp-app-integrations.md](../v6.1.0/implemented/mcp-app-integrations.md) + [mcp-sandbox-separate-origin.md](../v6.1.0/implemented/mcp-sandbox-separate-origin.md) (the separate-origin sandbox mechanics reused for defense-in-depth), [a2ui-over-mcp.md](../v6.5.0/a2ui-over-mcp.md) (the tiering decision tree this extends)
**Created**: 2026-07-09
**Last Updated**: 2026-07-09

> **Origin:** Written after examining CopilotKit's
> [OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) (from the
> AG-UI maintainers) alongside our own in-progress experiment letting the agent
> emit ` ```svg ` blocks ([SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx)).
> OpenGenerativeUI has the LLM author **HTML + CSS + JavaScript** per turn and
> boots it in a sandboxed iframe. This doc scopes a **deliberately narrower**
> capability: AI-authored **HTML/CSS only, sanitized, no script execution** —
> the tier between static SVG and a pre-authored MCP App iframe. The conscious
> rejection of LLM-authored JS is the whole point; see *The conscious
> divergence* below.

## Problem Statement

The agent can present its output at two very different points on a spectrum, with
a large gap in the middle:

- **Low end — sanitized SVG** ([SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx)):
  the agent emits a ` ```svg ` fenced block; we DOMPurify it (scripts, `use`,
  and `href`/`xlink:href` stripped to block SSRF) and inline it. Safe, but
  **static and visual-only** — no layout system, no flowing text alongside
  shapes, no tables, no interactivity, no design-system styling. It is a picture,
  not a UI.
- **High end — MCP App iframe** ([MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx)
  + `<AppRenderer>` on a separate-origin sandbox): full HTML/CSS/JS, but the app
  is **pre-authored and served by a tool** (the CesiumJS-class case). The agent
  cannot generate one on the fly; a developer builds and ships it.
- **Structured middle — A2UI** ([A2UISurfaceMount.tsx](../../../frontend/src/components/protocols/A2UISurfaceMount.tsx)):
  declarative catalog components (cards, tabs, lists, forms). This is and remains
  the **default** for structured UI. But the catalog is finite — it cannot
  express an arbitrary bespoke layout, an annotated diagram with flowing labels,
  a custom comparison grid, or a one-off presentational treatment the Basic
  catalog has no vocabulary for.

**The gap:** there is no path for the agent to produce **rich, laid-out,
on-the-fly presentational UI** — richer than a flat SVG, more bespoke than the
A2UI catalog — **without** a developer pre-authoring an MCP App. Today, the
moment the catalog can't express something, the only escalation is a full
HTML/JS iframe app that must be built by hand.

**Current State / pain points:**

- **SVG is a dead picture.** No layout, no flowing content, no host styling. An
  agent that wants "a labelled diagram with a caption and two linked callouts"
  has to hand-draw all of it in SVG coordinates, and it still can't match brand
  typography or be clicked to drive a follow-up.
- **The only richer path executes arbitrary code.** OpenGenerativeUI's answer —
  and the naive answer — is "let the model write JS and sandbox it." Given this
  platform renders **confidential customer content** (Acme Energy PPAs; see
  [CLAUDE.md](../../../CLAUDE.md) Security Hard Rules), shipping an
  LLM-authored-JS execution path as a *default* rendering primitive is a trust
  surface we should not open without a specific, justified need.
- **No design-system fidelity for generated UI.** Per-deploy branding (each
  deployment *is* the brand — no runtime tenant override) means any generated UI
  must inherit host tokens. Raw SVG and third-party iframes both fail this.

**Impact:** Skill/tool authors who want a bespoke presentational view today must
either accept flat SVG or build a full MCP App. No end-user-visible bug — this is
a **capability gap**, which is why it is **design-ahead, gated on a consumer**,
not scheduled work.

## The capability ladder (what this adds, and where)

This design adds **Tier 2** — and, by construction, makes the agent prefer the
*least powerful* tier that expresses the content. The ladder is the security
model: power is escalated only when the tier below genuinely can't do the job.

| Tier | Path | Authored by | Executes code? | Status |
|---|---|---|---|---|
| 0 | **A2UI catalog** (`A2UISurfaceMount`) | Agent picks catalog components | No (declarative) | ✅ default for structured UI |
| 1 | **Sanitized SVG** (`SVGBlock`) | Agent draws SVG | No (scripts stripped) | ✅ today |
| **2** | **Generative HTML/CSS surface** (this doc) | Agent authors HTML/CSS | **No — sanitized, no `<script>`** | 🆕 proposed |
| 3 | *Sandboxed JS* (OpenGenerativeUI parity) | Agent authors HTML/CSS/**JS** | Yes, in a no-network sandbox | ❌ **explicitly out of scope** — see divergence |
| 4 | **MCP App iframe** (`<AppRenderer>`) | Developer pre-authors | Yes (trusted, pre-shipped) | ✅ today (Cesium-class) |

Tier 2 fills the gap between 1 and 4 **without** crossing into Tier 3. It is
"HTML as a richer SVG" — layout, typography, tables, flowing content, host
styling, and click-to-act — with the **same script-free safety envelope** SVG
already has.

## Where v6 already aligns with OpenGenerativeUI

Useful framing: several of their good ideas we can adopt *without* adopting their
core (LLM-authored JS).

| OpenGenerativeUI concept | Adopt in Tier 2? | How |
|---|---|---|
| **Design-system injection** (`assemble_document` wraps generated HTML with host CSS) | ✅ Yes | Inject host CSS custom properties / tokens into the sandbox so generated UI matches per-deploy branding by construction. |
| **Streaming morph preview** (Idiomorph re-renders as the tool streams) | ✅ Yes | Render the sanitized HTML incrementally as it streams — an INSTANT-FEEL win, and it works fine without JS. |
| **Narrow host bridge** (`sendPrompt` / `openLink`, Zod-validated) | ✅ Yes, but no JS | We don't need a JS bridge: interactivity is expressed as declarative click targets that route through the **existing surface-action gate** (clicks → agent turn), not an in-iframe JS postMessage bridge. |
| **iframe sandbox isolation** | ✅ Yes (defense-in-depth) | Reuse the separate-origin sandbox even though there's no JS to contain — belt-and-suspenders against sanitizer bypass. |
| **Skills / progressive disclosure for UI guidance** | ✅ Already have | Our Agent Skills + `agent-protocols` skill already carry UI-authoring guidance; add a "when to emit a generative surface vs A2UI" note. |
| **LLM authors executable JavaScript** | ❌ **No** | The conscious divergence — see below. |

### The conscious divergence worth recording

OpenGenerativeUI's central bet is **LLM-authored JavaScript executed in a
sandbox**. We are deliberately **not** taking that bet at this tier. Recording
why, so a future reader of their repo does not conclude we "missed" it:

- **Confidential content raises the stakes.** This platform renders customer
  contracts and financials. A script-free declarative-ish payload (sanitized
  HTML) that *cannot execute* is a categorically smaller trust surface than
  arbitrary JS, even sandboxed. The [CLAUDE.md](../../../CLAUDE.md) security rule
  is architectural, not advisory.
- **Non-determinism vs EARNED TRUST.** Their approach *requires* a high-capability
  model and still occasionally produces broken layouts. Script-free HTML fails
  more gracefully (a malformed tag is dropped by the sanitizer; a malformed JS
  program can misbehave arbitrarily).
- **We already have the JS-needed escape hatch.** When a UI genuinely needs
  client-side execution (a 3D globe, a live simulation), that is **Tier 4** — a
  pre-authored, developer-reviewed MCP App, not LLM-authored JS. The Cesium
  fixture is exactly this. Tier 3 (LLM-authored JS) would sit *between* them and
  is intentionally left unbuilt; if a real consumer ever needs it, it gets its
  own design doc with its own security review, referencing OpenGenerativeUI as
  the reference implementation.

## Goals

**Primary Goal:** Let the agent (or a tool result, via the
[tool-results-as-a2ui.md](tool-results-as-a2ui.md) emission path) produce a
**sanitized, script-free, host-styled HTML/CSS surface** on the fly, rendered by
a single generic mount — richer than SVG, no bespoke React, no code execution,
behind the same access gate as any other surface.

**Success Metrics:**

- The agent can emit a `generative-ui` payload (HTML/CSS) that renders as a
  laid-out, host-branded surface with **zero new React per surface** and **zero
  `<script>` execution** (sanitizer-verified).
- Interactivity (a clicked element → a follow-up agent turn) routes through the
  **existing surface-action gate** — no new action path, no in-iframe JS.
- A sanitizer rejection, budget overflow, or unknown payload **degrades**
  (falls back to A2UI / `SVGBlock` / code) — never renders unsafe content.
- Generated UI inherits per-deploy branding tokens (visually consistent with host
  chrome), verified against a fixture.

**Non-Goals:**

- **Tier 3 — LLM-authored JavaScript execution.** Explicitly out of scope (see
  divergence). If needed later, separate doc + security review.
- Replacing A2UI for structured UI. A2UI (Tier 0) stays the default; this is the
  escalation for what the catalog *can't* express.
- Replacing MCP Apps (Tier 4) for stateful/executable modules.
- A visual builder / WYSIWYG for generated UI.
- Persisting generated HTML to any public/CDN location (see Security).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Streaming morph preview renders the surface as it's authored — "watching it build" vs waiting for a complete payload. |
| 2 | EARNED TRUST | 0 | Presentational only. Constrained to rendering data the agent already holds/cited; citations are preserved as click-through targets. Does not create a path to present *uncited* claims (a guardrail, see Security). |
| 3 | SKILLS, NOT FEATURES | +1 | Any skill/tool gains a bespoke rich view without an app-code PR — the escalation tier above the catalog. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Least-powerful-tier-first: most UI stays on the no-LLM declarative A2UI path; HTML generation escalates to the smart tier **only** when the catalog can't express it. |
| 5 | GRACEFUL DEGRADATION | +1 | Sanitizer failure / budget overflow / unknown payload falls back to A2UI or `SVGBlock`/code. Fail-safe to a path we already ship. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Honest tension: raw HTML is *less* protocol-structured than A2UI. Mitigated by keeping A2UI the default (this is the explicit escape hatch for what the catalog can't cover) and routing all **interaction** through the existing surface-action protocol — no new wire format is invented. Not −1 because it is additive at the presentational edge where no protocol vocabulary exists, and does not bypass or replace the protocol paths. |
| 7 | API FIRST | +1 | The surface is data (an HTML/CSS string + metadata) over the existing transport — inspectable, testable, CLI-previewable headlessly. |
| 8 | OBSERVABLE BY DEFAULT | +1 | One shared sanitize+mount path; sanitizer rejections and budget overflows are logged/traced once, not per surface. |
| 9 | SECURE BY CONSTRUCTION | +1 | **No script execution** (sanitizer-enforced), separate-origin no-network sandbox for defense-in-depth, no external resource loads, authed session, never a public bucket. Strictly smaller trust surface than the Tier-4 iframe, and than any JS-executing alternative. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | The client is a thin generic sanitize+mount; shaping is server/agent-side. Neutral — the "fat" part (the HTML) is authored by the model, not client business logic. |
| | **Net Score** | **+7** | Threshold ≥ +4 ✓ — strong alignment. No axiom scores −1. |

**Conflict Justifications:** None (no axiom scored −1). The PROTOCOL-OVER-CUSTOM
tension is scored 0 and mitigated as noted, not −1.

**Hard-fail check:** EARNED TRUST is 0 (not −1) though the feature is
user-facing — mitigated by the presentational-only guardrail. SECURE BY
CONSTRUCTION is +1 though the feature renders new content — it introduces **no
new data access** (renders data the agent already holds) and **no execution**.
Passes.

## Design

### Overview

The transport and both halves of the mechanism already exist. The agent-emitted
path rides the same AG-UI channel A2UI does; the tool-result path reuses the
[tool-results-as-a2ui.md](tool-results-as-a2ui.md) **Model-B emission** (a
server-side mapping pushes a surface message via a `CUSTOM` event, out of the
model's context). The **only new thing** is a render path: when a surface message
carries `kind: "generative-html"` (vs A2UI component messages), the mount
**sanitizes + host-styles + sandboxes** the HTML instead of running the A2UI
renderer.

```
Agent/tool produces a generative-html surface payload
  { kind: "generative-html", html, styleTokens, height?, actions? }
                     │
        (rides existing A2UI/CUSTOM surface channel — no new transport)
                     │
   GenerativeUISurface mount (NEW, generic — no per-surface React)
                     │
   ┌─────────────────┴─────────────────────────────────────────┐
   │ 1. SANITIZE   DOMPurify (HTML profile, FORBID <script>,    │
   │               event handlers, external href/src) —         │
   │               generalises SVGBlock's PURIFY_CONFIG          │
   │ 2. BUDGET     reject > size/height budget → degrade         │
   │ 3. STYLE      inject host design-system tokens (branding)   │
   │ 4. MOUNT      separate-origin sandbox iframe (no network,   │
   │               no same-origin) — defense-in-depth            │
   │ 5. STREAM     morph incremental HTML as it arrives          │
   └────────────────────────────────────────────────────────────┘
                     │
   click on a declarative action target
                     │
   existing surface-action gate → agent turn  (NO in-iframe JS)
```

### The safety envelope (the heart of "safe and controlled")

Five layers, each independently sufficient to block script execution — the design
does not rely on any single one:

1. **Sanitize (primary).** Generalise
   [SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx)'s
   `PURIFY_CONFIG` from an SVG profile to a full-HTML profile:
   `FORBID_TAGS: ["script", "iframe", "object", "embed", "link", "meta", "base"]`,
   forbid all `on*` event-handler attributes, forbid external `href`/`src`/
   `xlink:href` (SSRF + tracking-pixel + exfil-beacon prevention), allow only a
   curated tag/attr set (structural + text + table + `style` attr with a
   property allowlist). Named constant, reviewable in one place, exactly as
   SVGBlock does today.
2. **No external loads.** No `<img src=…>` to arbitrary origins, no web fonts,
   no `@import`. Images, if allowed at all, must be host-served (a follow-up;
   v1 is text/shape/layout only). This closes the "render a 1×1 beacon to
   exfiltrate confidential text" vector.
3. **Separate-origin sandbox (defense-in-depth).** Reuse the MCP-App
   separate-origin sandbox proxy
   ([mcp-sandbox-separate-origin.md](../v6.1.0/implemented/mcp-sandbox-separate-origin.md)):
   `sandbox` attribute *without* `allow-scripts` and *without* `allow-same-origin`,
   CSP `default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'`. Even
   if the sanitizer is bypassed, the frame cannot execute JS, reach the network,
   or read host storage. (Note: this is a genuine second wall — SVGBlock today
   renders inline via `dangerouslySetInnerHTML`; full HTML warrants the iframe.)
4. **Budget.** Reject payloads over a size and rendered-height budget → degrade.
   Prevents a runaway generation from producing a multi-megabyte DOM.
5. **Tiering (governance).** Generative HTML is escalation-only. The agent is
   instructed (skill guidance) to prefer A2UI (Tier 0) and use Tier 2 only when
   the catalog can't express the content — keeping most renders on the safest,
   deterministic, no-LLM path.

### Design-system injection (brand fidelity)

Inject the host's CSS custom properties (the same tokens the app shell uses) into
the sandbox document `<head>` before mount, so generated UI inherits typography,
colour, and spacing by construction — the OpenGenerativeUI `assemble_document`
idea, adapted. This is what keeps generated UI on-brand under the
per-deploy-branding stance (no runtime tenant override).

### Streaming preview

Render the sanitized HTML incrementally as the payload streams (DOM-morph the
sandbox body, à la Idiomorph). Because there is no JS, morphing is safe and
cheap. INSTANT-FEEL win: the surface visibly assembles instead of popping in at
`TOOL_CALL_END`.

### Interaction — declarative, through the existing gate

No in-iframe JS bridge. Instead, the payload may mark elements with a declarative
action token (e.g. `data-surface-action="..."`); the mount intercepts clicks at
the sandbox boundary and routes them through the **existing surface-action
loop** ([action-triggered-agent-turn.md](../v6.1.0/implemented/action-triggered-agent-turn.md)),
Firebase-auth + session + skill-allowlist gated — identical to A2UI actions. This
gives click-to-act (the useful 80% of OpenGenerativeUI's `sendPrompt` bridge)
with zero script execution.

### Frontend Changes

**New: `GenerativeUISurface.tsx`** — a generic mount (sibling to
`A2UISurfaceMount` / `MCPAppToolCallRouter`) implementing the 5-layer envelope
above. No per-surface React.

**Modified: the surface render switch** — where a workspace/chat surface message
is dispatched today (A2UI vs MCP-App), add a `kind: "generative-html"` branch →
`GenerativeUISurface`. Unknown kind → degrade.

**Reused (no change):** the surface-action POST path; the separate-origin sandbox
proxy; DOMPurify (already a dependency).

### Backend Changes

Minimal, and only for the tool-result path:

- **Reuse the Model-B emission** from
  [tool-results-as-a2ui.md](tool-results-as-a2ui.md): a result→surface mapping may
  return a `generative-html` surface message instead of A2UI component messages,
  pushed via the same `CUSTOM` event (out of model context). Marked as a UI
  payload so it is **never offloaded** (the generalised `_RENDER_PAYLOAD_TOOLS`
  marker).
- **Agent-emitted path:** a thin toolset verb (sibling to
  `send_a2ui_json_to_client`) that accepts an HTML/CSS string + metadata. The
  backend does **not** need to sanitize (the host sanitizes at ingress, which is
  where the trusted render happens) but **should** enforce the size budget and
  strip obvious `<script>` server-side as a first cheap gate + for cleaner traces.

### Standards Compliance Check

- **No open standard exists** for "agent-authored sanitized HTML surface." A2UI
  (the structured-UI standard) is deliberately kept as the default; this is the
  escape hatch for what its catalog can't express, and it does **not** invent a
  wire format — it reuses the A2UI/`CUSTOM` surface channel and the surface-action
  protocol. Interaction is 100% on the existing protocol path. Because it adopts
  the surrounding protocols and only adds a presentational `kind`, it does not
  score −1 on Axiom 6 (scored 0, tension noted).
- **CSP / iframe `sandbox`** are the relevant web standards for the isolation
  layer — adopted verbatim, not reinvented.

### CLI Surface

Per the CLI-affordance rule — a small headless aid so the safety envelope is
verifiable without a browser:

- `aiplatform ui render <payload.json>` — run the sanitizer + budget + CSP
  assembly on a `generative-html` payload and print (a) the sanitized HTML,
  (b) any stripped tags/attrs, (c) pass/fail against budget. Answers "what
  exactly will render, and what got stripped?" headlessly. ~0.25d. Backlink:
  [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

### API Changes

None. No new/modified HTTP endpoints. The surface rides the existing AG-UI
surface channel; interaction rides the existing surface-action endpoint.

## Implementation Plan

> Gated — do not start without a consumer (a skill/tool that genuinely needs
> bespoke presentational UI the A2UI catalog can't express).

### Phase 0 — Spike (~0.25d)
- [ ] Confirm the DOMPurify full-HTML profile strips `<script>` + `on*` + external
      `href`/`src` on a hostile fixture (extend the existing SVGBlock XSS fixture
      in [dev/rich-media](../../../frontend/src/app/dev/rich-media/page.tsx)).
- [ ] Confirm the separate-origin sandbox proxy accepts a static HTML document
      with `connect-src 'none'` (no MCP-App JS bridge) and renders it.

### Phase 1 — Sanitize + mount (~1d)
- [ ] `GenerativeUISurface.tsx`: sanitize → budget → design-system inject →
      separate-origin sandbox mount.
- [ ] Surface render switch: `kind: "generative-html"` branch; unknown → degrade.

### Phase 2 — Streaming + interaction (~0.75d)
- [ ] Incremental morph render as the payload streams.
- [ ] Declarative `data-surface-action` click interception → existing
      surface-action gate.

### Phase 3 — Emission + CLI + tests (~0.5d)
- [ ] Backend: agent-emit verb + Model-B mapping may return `generative-html`;
      UI-payload marker (no offload); server-side budget gate.
- [ ] `aiplatform ui render` verb.
- [ ] Tests (below). chrome-devtools browser verify.

## Migration & Rollout

**No data migration.** Pure additive render path. **Feature flag** (default off)
until a consumer is wired, so A2UI + SVG remain the only rendering paths in
production until deliberately enabled. **Rollback:** remove the branch — Tier 0/1
untouched.

## Testing Strategy

### Frontend (Vitest)
- [ ] Hostile HTML (`<script>`, `<img onerror>`, external `<img src>`,
      `<iframe>`, `<a href=javascript:>`) → all stripped; safe structure renders.
- [ ] Payload over budget → degrades (no render), logs.
- [ ] `kind: "generative-html"` → `GenerativeUISurface`; `kind: a2ui` →
      `A2UISurfaceMount` (regression guard); unknown kind → degrade.
- [ ] A `data-surface-action` click POSTs to the surface-action path.
- [ ] Design-system tokens present in the mounted document.

### Backend (pytest)
- [ ] Agent-emit verb enforces the size budget and strips `<script>` server-side.
- [ ] A `generative-html` UI payload is never offloaded (generalised marker).

### Manual
- [ ] chrome-devtools: render a generated surface, confirm it is on-brand,
      script-free (no console execution), no network requests leave the sandbox
      (Network panel), and a click drives an agent turn.

## Security Considerations

This is the section that gates the whole doc.

- **No script execution — enforced twice.** Sanitizer forbids `<script>`/`on*`;
  the sandbox iframe omits `allow-scripts`. Either alone blocks JS; both are
  present by design.
- **No egress from the sandbox.** CSP `connect-src 'none'` + no external
  `href`/`src` + no `allow-same-origin` means confidential data rendered inside
  the surface **cannot be exfiltrated** — no beacon, no fetch, no form POST to a
  third party. This is the direct answer to the [CLAUDE.md](../../../CLAUDE.md)
  confidential-content rule: the surface adds **no new egress path**.
- **Never persisted publicly.** The generated HTML rides the authed session and
  is rendered client-side; it is **never** written to a public bucket or CDN
  (contrast the demo-fixture public bucket). A derivative of a private contract
  stays behind the auth gate, always. Explicit non-goal.
- **No new data access.** The surface renders data the agent already legitimately
  holds; it does not read new sources. (Hence SECURE-BY-CONSTRUCTION +1 despite
  new rendering.)
- **EARNED-TRUST guardrail (presentational-only).** Generative HTML is for
  *presentation* of data the agent has and has cited — not a channel to smuggle
  uncited claims into a pretty layout. Skill guidance must state this, and
  citations should be preserved as click-through `data-surface-action` targets,
  not flattened into decorative text. (This is why EARNED TRUST is scored 0, not
  +1 — the feature is trust-neutral only if used as intended.)
- **Residual risk — UI spoofing.** Legitimate HTML can be arranged to mislead
  (a fake "confirm" button). Not worse than today's Tier-4 iframe (which can
  render anything), and mitigated because actions route through the same
  auth-gated surface-action path — a spoofed button cannot do more than a real
  one. Worth a note at implementation, not a blocker.
- **Explicitly deferred: LLM-authored JS (Tier 3).** Not in this design. If ever
  needed, it requires its own doc + security review; it is a strictly larger
  trust surface than everything here.

## Performance Considerations

- Sanitize + morph render is cheap; no JS parse/execute cost (vs Tier 3/4).
- Generation routes to the smart tier (better HTML) — a token/latency cost paid
  **only** on escalation, since Tier 0 (A2UI, no LLM) stays the default. Aligns
  with RIGHT-MODEL-RIGHT-MOMENT.
- Separate-origin sandbox boot has a small handshake cost (already paid by the
  MCP-App path); acceptable for the escalation tier, and streaming preview hides
  it.

## Success Criteria

- [ ] The agent/a tool can render a sanitized, host-styled HTML surface via one
      generic mount — no bespoke React per surface.
- [ ] No `<script>` executes and no request leaves the sandbox (verified in
      browser Network + console).
- [ ] A clicked action drives an agent turn through the existing surface-action
      gate — no in-iframe JS.
- [ ] Generated UI inherits per-deploy branding tokens.
- [ ] Sanitizer/budget failure degrades gracefully to A2UI/SVG/code.
- [ ] `aiplatform ui render` previews the sanitized output + strip report headlessly.
- [ ] Tier 3 (LLM-authored JS) is documented as out of scope with a pointer to
      OpenGenerativeUI as the reference if ever revisited.

## Open Questions

- **Where does the agent decide Tier 0 vs Tier 2?** Skill-prompt guidance ("prefer
  A2UI; use a generative surface only when the catalog can't express it") vs a
  post-hoc heuristic. Leaning prompt guidance in the `agent-protocols` skill,
  mirroring how A2UI authoring guidance already lives there.
- **Host-served images in v2?** v1 is text/shape/layout/table only (no external
  loads). A later increment could allow images via a host proxy (same auth gate),
  never external URLs. Gated on need.
- **First real consumer?** Candidates: a skill wanting a bespoke annotated diagram
  or comparison grid the A2UI catalog can't express; the SVG experiment growing a
  demand for flowing text + layout around the shapes. Until one exists, resting
  state is this doc.
- **Reuse `MCPAppToolCallRouter`'s sandbox plumbing or a sibling mount?** Leaning
  a sibling (`GenerativeUISurface`) that imports the shared separate-origin
  sandbox helper — the MCP-App router carries JS-bridge machinery this tier
  deliberately doesn't want.

## Related Documents

- [tool-results-as-a2ui.md](tool-results-as-a2ui.md) — the Model-B result→surface emission path this reuses for the tool-result variant
- [a2ui-over-mcp.md](../v6.5.0/a2ui-over-mcp.md) — the tiering decision tree (structured → A2UI; stateful → iframe) this extends with Tier 2
- [SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx) — the DOMPurify sanitize pattern generalised here from SVG to full HTML
- [mcp-app-integrations.md](../v6.1.0/implemented/mcp-app-integrations.md) + [mcp-sandbox-separate-origin.md](../v6.1.0/implemented/mcp-sandbox-separate-origin.md) — the separate-origin sandbox reused for defense-in-depth
- [action-triggered-agent-turn.md](../v6.1.0/implemented/action-triggered-agent-turn.md) — the surface-action loop interaction rides
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance backlink
- [CLAUDE.md](../../../CLAUDE.md) — Security Hard Rules (confidential content) + Principle #7 (protocols-first UI)
- [Product Axioms](../../product-axioms.md)

## Sources

- [CopilotKit / OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) — the reference implementation of the LLM-authored-HTML/CSS/JS-in-sandbox approach (Tier 3+): `generateSandboxedUi` streaming tool, `OpenGenerativeUIMiddleware`, `WebSandbox` iframe, Idiomorph streaming preview, Zod-validated `sendPrompt`/`openLink` bridge, `assemble_document` design-system injection. This doc adopts its *presentational* ideas (design-system injection, streaming preview, click-to-act) while **rejecting** its core (LLM-authored JS execution).
- Codebase, verified 2026-07-09: [SVGBlock.tsx](../../../frontend/src/components/chat/media/SVGBlock.tsx) (`PURIFY_CONFIG`, script/`use`/`href` strip), [A2UISurfaceMount.tsx](../../../frontend/src/components/protocols/A2UISurfaceMount.tsx), [MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx), [dev/rich-media/page.tsx](../../../frontend/src/app/dev/rich-media/page.tsx) (SVG-with-injected-script XSS fixture to extend).
