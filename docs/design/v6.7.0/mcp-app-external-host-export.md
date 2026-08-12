# MCP-App External-Host Export (ChatGPT / Copilot / Claude as 3rd-party hosts)

**Status:** Proposed — roadmap / design-ahead, **gated** (nothing built until a
real consumer + the security-consent model are decided). Sprint key
`EXT-MCP-EXPORT`. Priority P3.

**Author:** platform (adapted from the AIPLA fork's `EXT-MCP` + `SHARED-BRIDGE`
sprints, which shipped this for public physics sims — verified live in ChatGPT
2026-07-04).

**Date:** 2026-07-13

---

## Problem Statement

Platform's MCP-App artefacts (the PPA obligation analysis, future what-if
surfaces) render **only inside platform's own host** — the `StaticArtefactFrame`
→ `sandbox.html` chain. An external MCP host (ChatGPT Apps, Microsoft 365
Copilot, Claude Desktop, Goose) that connects to platform's `/mcp` server today
gets **text-only tools**: `mcp_server.py` exposes each public skill as a
`skill_<id>` tool whose handler returns a `str`. There is no `ui://` resource, no
`_meta.ui.resourceUri` on any tool, and no `structuredContent` in results — so an
external host has nothing renderable to mount.

The roadmap ask: let a customer drop a platform artefact (e.g. an obligation
analysis) into ChatGPT as a **3rd-party app**, so it renders and stays
interactive in a host we don't own.

**Why this is not a small lift, and why it's gated:** the whole reason platform
uses a double-iframe on a separate origin is to keep **confidential customer
content inside the Aitana GCP edge** (CLAUDE.md § Security Hard Rules). Rendering
an artefact inside ChatGPT means the artefact HTML **and the data it needs** run
on OpenAI's infrastructure, in OpenAI's sandbox. For a customer-confidential PPA,
that is a direct violation of the security rule. So external export is viable
**only** for artefacts whose content is non-confidential (public demos, synthetic
data, customer-consented exports) — and the consent/data-boundary model is the
real design work, not the wire plumbing. That tension is why this ships later,
if at all.

## The capability ladder (where this sits)

| Tier | Surface | Host | Content boundary |
|---|---|---|---|
| A2UI | declarative JSON | platform host | inside edge |
| MCP-App iframe | HTML/JS/WASM | **platform host** (today) | inside edge (double-iframe) |
| **MCP-App export** | same artefact HTML | **external host** (this doc) | **leaves the edge — gated** |

This doc adds the third row. The artefact HTML can be identical; what changes is
(1) the server exposes it as an MCP UI resource, (2) the guest bridge speaks the
external host's channel, and (3) — the crux — the **data path** must not leak
confidential content to the external host.

## What the AIPLA fork built (the reference implementation)

Two sprints, both verified live in ChatGPT:

1. **`EXT-MCP` — serve artefacts as a public MCP *server*.**
   - `backend/protocols/sim_apps.py` registers each artefact as a
     `ui://aipla/<name>/<version>` `FunctionResource` (`text/html;profile=mcp-app`,
     lazy `load_html`: filesystem in dev/CI, `httpx` from `MCP_SANDBOX_URL` in
     prod) plus a `show_<name>` tool.
   - **Dual `_meta` for host compatibility** — every tool/resource emits BOTH the
     standard keys and the OpenAI aliases:
     ```python
     meta={"ui": {"resourceUri": uri, "visibility": ["model", "app"]},
           "openai/outputTemplate": uri}                       # tool
     # resource: "ui": {"csp": {...}}, "openai/widgetCSP": {...}
     ```
   - Transport hardening: `streamable_http_path="/"` (avoids `POST /mcp/mcp`
     404), `stateless_http=True`, a trailing-slash proxy route to dodge FastMCP's
     307 that broke streaming-body replay.

2. **`SHARED-BRIDGE` — the actual "ChatGPT is blind" fix.** Root cause: ChatGPT
   does **not** listen for `ui/update-model-context` postMessage; it exposes a
   `window.openai` object. The sims were postMessage-only, so they rendered but
   couldn't talk to ChatGPT's model. Fix — a canonical **guest bridge** with BOTH
   channels:
   - `window.parent.postMessage({method:"ui/update-model-context", …})` — SEP-1865,
     read by platform's own host, M365 Copilot (native SEP-1865), and Claude.
   - `window.openai.setWidgetState(state)` + `window.openai.sendFollowUpMessage(
     {prompt})` — the ChatGPT channel.
   - **Host detection** via the `ui/initialize` reply `serverInfo.name` (ours vs
     external) and `window.openai` presence (ChatGPT).
   - A `content:[{type:"text",text}]` block added so generic hosts feed the model
     text. Copilot came free (native SEP-1865); only server-side CORS + OAuth
     differed.

## What platform already has (don't rebuild)

- **`window.openai` shim is already in the obligation artefact.** `index.html`'s
  `emitModelContext()` already calls `window.openai.setWidgetState(...)` when
  present, alongside the postMessage. So the artefact is partway to ChatGPT-aware;
  the missing half is server-side UI-resource exposure + a full guest bridge
  (`sendFollowUpMessage`, host detection, host-fit helpers).
- **`/mcp` server** (`backend/protocols/mcp_server.py`, FastMCP "aitana-platform",
  `streamable_http_path="/"`, `stateless_http`, public-only) — the transport
  substrate exists; it just serves text tools, not UI resources.
- **`MCPAppToolCallRouter` + `@mcp-ui/client`** — platform can already *consume*
  external artefacts as a host; this doc is the inverse (platform as the exported
  server).

## Goals

1. An external MCP host connecting to platform's `/mcp` can **discover and render**
   an allow-listed, **non-confidential** artefact as an MCP App (ChatGPT + Copilot
   + Claude), interactive, with the model seeing what the user does.
2. **One canonical guest bridge**, inlined into artefacts by a build step and
   CI-drift-guarded — no per-artefact channel code, no 4-copies drift (the exact
   bug the fork hit).
3. **The confidential-content rule is preserved by construction** — an artefact is
   exportable only if explicitly marked non-confidential (or the data path is
   per-user OAuth-gated and customer-consented). Default: not exportable.

## Non-Goals

- Exporting **confidential customer** artefacts (PPAs on real contracts) to a
  3rd-party host without an explicit, per-customer consent + data-boundary
  decision. Out of scope until that model exists.
- Auth/identity federation with external hosts beyond what the MCP + host OAuth
  specs already provide.
- Changing the platform-internal host path (this is purely additive).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral — external render latency is the host's, not ours. |
| 2 | EARNED TRUST | +1 | The verified-reasoning artefact (Z3-proved settlement) is exactly what earns trust in a *new* host — but only if the data boundary holds. |
| 3 | SKILLS, NOT FEATURES | +1 | A skill's artefact becomes reachable wherever the user already works (ChatGPT), not just our app. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Neutral. |
| 5 | GRACEFUL DEGRADATION | +1 | Dual-channel bridge + host detection degrade cleanly across hosts (postMessage-only hosts still work). |
| 6 | PROTOCOL OVER CUSTOM | +1 | Pure MCP + MCP-Apps + SEP-1865; the only "custom" is the `openai/*` alias, which IS the ChatGPT interop standard. |
| 7 | API FIRST | +1 | The export IS an API surface — a public MCP server, discoverable and callable. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Once rendered in an external host, telemetry is the host's — a visibility *loss* offset by out-of-edge being the explicit tradeoff. |
| 9 | SECURE BY CONSTRUCTION | -1 | **The tension.** Content crosses the Aitana edge to a 3rd-party renderer. Acceptable ONLY for non-confidential artefacts / consented data paths, enforced by an exportability gate (see Justification). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The artefact is a thin client of an MCP protocol boundary reused by any host. |
| | **Net Score** | **+5** | Threshold: >= +4 |

**Conflict Justifications:**
- **#9 (-1):** exporting to an external host inherently moves a derivative of
  content outside the GCP edge — the same class of decision as the Langfuse-Cloud
  ruling (data crossing the project edge needs explicit justification). The
  mitigation is **not** technical hardening of the leak; it is an **exportability
  gate by construction**: an artefact is exposed over `/mcp` as a UI resource only
  when its skill/artefact metadata marks it `external_export: allowed` AND its
  data is non-confidential (public/synthetic) OR the per-user OAuth + customer
  consent path is satisfied. Deny-by-default; a confidential PPA can never be
  exported by accident. Because the gate is the whole point, this stays a **gated,
  design-ahead** doc — the score is honest, not hidden.

## Design

### Server — expose artefacts as MCP UI resources (`backend/protocols/`)

New module (mirror the fork's `sim_apps.py`): `register_mcp_app_exports(mcp)`.
For each artefact whose metadata is `external_export: allowed`:
- Register a `FunctionResource` at `ui://aitana/<name>/<version>`
  (`text/html;profile=mcp-app`), lazily loading the artefact HTML from
  `MCP_SANDBOX_URL` (prod) / filesystem (dev). No Docker bake, no git dup.
- Register a `show_<name>` tool with **dual `_meta`** (`ui.resourceUri` +
  `openai/outputTemplate`; resource CSP under `ui.csp` + `openai/widgetCSP`).
- Result carries `structuredContent` (the artefact's initial `view`/payload —
  **synthetic/non-confidential only** under the gate).
Mount on the existing `/mcp` server; keep `streamable_http_path="/"`.

### Guest bridge — one canonical file, inlined + drift-guarded

`infrastructure/mcp-sandbox/bridge/aitana-mcp-bridge.js`: postMessage (SEP-1865)
**and** `window.openai` (`setWidgetState`, `sendFollowUpMessage`), host detection
via `serverInfo.name` + `window.openai`, `content` text block, and host-fit
helpers (`notifyIntrinsicHeight`, `widgetState`, `openExternal`). Inlined into
each artefact between `@aitana-bridge:start/end` markers by a
`scripts/build-artefact-bridge.mjs` (`make sim-build`), with a CI drift guard so
the four-copies bug can't recur. The obligation artefact's existing inline
`window.openai` shim collapses into this bridge.

### The exportability gate (the load-bearing part)

- New `external_export` field on artefact/skill metadata: `none` (default) |
  `allowed` (non-confidential) | `consented` (per-user OAuth + recorded customer
  consent). `register_mcp_app_exports` skips anything not `allowed`/`consented`.
- For `consented`, the data path must be per-user authenticated (host OAuth) and
  the payload fetched under that identity — never a public blob. Design TBD (Open
  Question 1).
- A CI check + a `scripts/audit-exportable-artefacts.sh` enumerate what is
  exportable, so a confidential artefact can never silently gain a `ui://` route.

### Transport

Reuse the fork's hardening: a trailing-slash `/api/mcp` proxy route (SSE
passthrough, `Mcp-Session-Id` round-trip, `duplex:"half"`), `stateless_http=True`,
CORS allowlist for the external hosts, DNS-rebind protection disabled for
server-to-server.

## Implementation Plan (gated — do not start without a consumer + OQ1 resolved)

- **Phase 0 — Spike (~0.5d):** stand up ONE synthetic/public artefact as a
  `ui://` resource + `show_` tool; render it in ChatGPT via the MCP connector.
  Proves the transport + dual-`_meta` end-to-end. No confidential data.
- **Phase 1 — Guest bridge (~1d):** canonical bridge + build step + CI drift
  guard; collapse the obligation artefact's inline `window.openai` shim into it.
- **Phase 2 — Export gate (~1d):** `external_export` metadata + gate in the
  register step + audit script + tests (deny-by-default proven).
- **Phase 3 — Consent path (gated on OQ1):** the `consented` data path. Not
  started until the customer-consent + per-user-OAuth model is decided.

## Testing Strategy

- **pytest:** `register_mcp_app_exports` skips non-`allowed` artefacts (gate
  deny-by-default); `ui://` resource serves the HTML; tool `_meta` carries both
  standard + `openai/*` keys.
- **Vitest/node:** the guest bridge emits on BOTH channels; host detection picks
  ChatGPT vs platform vs Copilot; drift guard fails a hand-edited artefact copy.
- **Manual:** render a synthetic artefact live in ChatGPT + Copilot + Claude;
  confirm the model sees a `setWidgetState`/`sendFollowUpMessage` round-trip.
- **Security:** an audit test asserting no confidential-tagged artefact is
  reachable via `/mcp` resources.

## Security Considerations

The single most important line in this doc: **a confidential customer artefact
must never be exportable by default.** The exportability gate is deny-by-default,
CI-audited, and the data path for `consented` artefacts must be per-user
authenticated. Everything else (CSP dual-emit, CORS allowlist, `stateless_http`)
is standard hardening; the gate is the architectural control. If Phase 0 can't be
done with purely synthetic data, it doesn't ship.

## Open Questions

1. **Consent + data boundary for confidential artefacts.** What does "customer
   consented, per-user OAuth data path" concretely look like — and is it ever
   worth it vs. keeping confidential artefacts platform-only? (This gates Phase 3
   and arguably the whole doc.)
2. **Which artefacts are ever exported?** Likely a public demo / a synthetic
   showcase — is there real customer demand for confidential-in-ChatGPT, or is the
   value entirely in a public marketing/demo surface?
3. **Maintenance cost** of tracking ChatGPT's `window.openai` surface as it
   evolves vs. the SEP-1865 standard the other hosts use.

## Related Documents

- `docs/design/v6.7.0/generative-ui-surface.md` — the sanitized-HTML tier (also a
  gated, design-ahead, security-first doc; same "safe ceiling" philosophy).
- `docs/design/v6.7.0/tool-results-as-a2ui.md` — the A2UI-first default this sits
  above.
- `.claude/skills/mcp-app-artefact/SKILL.md` — how platform builds artefacts today.
- `.claude/skills/agent-protocols/` — MCP / MCP-Apps / SEP-1865 spec references.

## Sources

- AIPLA fork sprints `EXT-MCP` + `SHARED-BRIDGE` (design doc
  `shared-mcp-app-bridge.md`, `external-host-mcp-apps.md`) — the reference
  implementation, verified live in ChatGPT 2026-07-04.
- MCP Apps spec (SEP-1865) + OpenAI Apps `window.openai` surface.
