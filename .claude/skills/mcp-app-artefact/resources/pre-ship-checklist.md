# Pre-ship checklist — MCP App artefact (platform)

Paste into the PR description before merging a new/changed artefact to `dev`.
Tick by ticking — the security + render gates have no exceptions.

## Confidential-content + ADR-013 security (NEVER skip)

- [ ] **No external loads.** `grep -nE "https?://|fetch\(|XMLHttpRequest|import\(['\"]http|@import|url\(https?" infrastructure/mcp-sandbox/artefacts/<name>/v<n>/` returns zero hits (inline everything; embed assets as data: or fetch same-origin from the sandbox).
- [ ] **No CDN / remote fonts / remote images** in CSS or HTML. System font stack or self-hosted only.
- [ ] **Contract data arrives via the host** (`ui/…/payload` notification), never a public URL. The artefact never phones home.
- [ ] **Strict artefact CSP intact** — the inner artefact loads under `serve.ts`'s `ARTEFACT_CSP` (`default-src 'none'`, inline-only). If it needs `unsafe-eval` (WASM), confirm that's scoped to the sandbox-proxy CSP, not a relaxation of the artefact CSP.
- [ ] **No derivative of private content is reachable without the auth gate** (CLAUDE.md § Security Hard Rules). If in doubt, stop and ask.

## Sizing + render (the recurring traps)

- [ ] **Content-height only** — no `height:100%`/fill hacks on body/main; natural content height (the proxy measures it → host sizes the iframe).
- [ ] **`_MCP_SANDBOX_URL` set** for the target env (root `cloudbuild.yaml` sub / trigger substitution) — else `NEXT_PUBLIC_MCP_SANDBOX_URL` bakes empty → blank on deployed.
- [ ] **`_ALLOWED_HOST_ORIGINS` includes every frontend origin** (run.app + custom domains), set via the `^@^` gcloud delimiter.
- [ ] **Verified in a REAL browser or a real AG-UI stream** — jsdom passing ≠ renders. Confirm `A2UI_SURFACE` emits AND `SurfaceRegistry` registers (artifactCount > 0), and the artefact fills to its content height.

## Talk-to-the-AI (if the artefact should be advisable)

- [ ] **Emits an AI-legible `view`** on each state change (the on-screen RESULT + active settings, not just inputs).
- [ ] **Wrapper mirrors `view`** into the surface data model (A2UI surface-context path) — confirmed reaching the prompt via `render_instruction_with_a2ui_surface_context` (or a live turn).
- [ ] (MCP-server path only) `serverId` in `tool_configs.mcp.servers` AND `allow_context_writes`; `structuredContent` ≤ 4 KB.

## Visual design (theme-aware)

- [ ] **Both themes** — `:root` (light) + `:root[data-theme="dark"]` palette; theme applied from `hostContext.theme` + `ui/update-theme`. Looks correct in both OS settings.
- [ ] **Semantic palette** (`--ok/--warn/--danger/--accent` + soft tints) — no ad-hoc colour literals.
- [ ] **Important figures stressed** (hero/instrument-display treatment for the number the user came for).
- [ ] **Responsive** — no fixed `min-width` > ~600px; body text ≥ 13px; fits the `md:w-1/2` pane without horizontal scroll.

## Wiring + tests

- [ ] **Host wrapper** authored (mirror `ObligationArtefactTab.tsx`) and wired into the workbench artifact-tab renderer.
- [ ] **Vitest** for the wrapper — payload push on init, event routing, `view` mirrored to the agent, cross-origin rejection.
- [ ] **JSON-RPC handshake block copied verbatim** from the exemplar (queued-until-init emit, ping responder, `ui/initialize`).
- [ ] **Sandbox build green** — `cd infrastructure/mcp-sandbox && npm run build`.

## Deploy

- [ ] Committed to `dev`; push rebuilds `mcp-sandbox` + `platform-frontend` (verify both Cloud Build triggers went SUCCESS, not just that the push landed).
- [ ] If skill-bound, the skill is seeded/available on the target env.
