# INTERNAL-SHELL Sprint Plan

**Design doc:** [internal-app-shell-port.md](internal-app-shell-port.md)
**Sprint key:** INTERNAL-SHELL
**Status:** Planned 2026-06-09
**Duration:** Wed 2026-06-10 → Thu 2026-06-11 (2 days, single dev)
**Scope:** Frontend only (~340 LOC + ~30 vitest)
**Runs parallel with:** 4.2 M4 CLI + smoke + rehearsal (backend/CLI — zero file overlap)

## Goal

Port six compositional UX patterns from gde-ap-agent's chat shell into Aitana so Friday 2026-06-12 ONE demo runs against a polished internal app, not just a polished landing.

## Day-by-day

### Wednesday 2026-06-10

**Morning — M1 SidebarShell (~1d total — most of Wed)**
- Port `SidebarSection.tsx` from gde-ap-agent chat-page lines 462–493 (verbatim, generic)
- Refactor existing sidebar in `app/chat/[...path]/page.tsx` to 3 collapsible sections: Sessions / Documents / History (locked Q1)
- Add `prevFreshChatRef` auto-collapse effect — fires once on isFreshChat true→false transition, skipped on resume
- 4 SidebarSection vitest + 2 sidebar-refactor vitest + 2 auto-collapse vitest

**Afternoon — M2 Workbench polish (~0.5d)**
- Patch `Workbench.tsx`: badge halo (animate-ping + dot), active underline (animate-in zoom-in), tab body fade-in
- Add default 4-breakpoint width scale (520/640/760/860px) when no className prop
- Extend `WorkbenchTab` interface with optional `emptyBody?: string` field
- New `EmptyTab.tsx` primitive
- 6 Workbench vitest + 2 EmptyTab vitest
- **Wed EOD checkpoint:** `npm run quality:check:fast` green; M1+M2 commit

### Thursday 2026-06-11

**Morning — M3 chat-shell ergonomics (~0.5d)**
- Port `SignInRequired.tsx` from gde-ap-agent lines 524–549; replace `router.replace("/")` early-return in ChatPage
- Port `InContextBadge.tsx` from gde-ap-agent lines 626–645; insert above composer
- 2 SignInRequired vitest + 3 InContextBadge vitest + 1 chat-page swap vitest + 1 InContextBadge insertion vitest

**Afternoon — M4 workspace-into-tab + race-guard + verify (~0.5d)**
- Extend chat-page surface-mount detection: when a Workbench tab id is `"workspace"`, mount A2UISurfaceMount INTO the tab; backwards-compat fallback to flex-sibling WorkspaceSurfaceRegion (Q4 locked)
- Race-guard audit: grep `fetchWithAuth(.*sessions.*state` in frontend; add `cancelled` ref flag where missing
- 4 workspace-into-tab vitest + 2 race-guard vitest
- Build + deploy via `git push origin dev`
- chrome-devtools MCP verify on live URL `/chat/@aitana-platform/one-doc-compare`; curl fallback if MCP locked
- **Thu EOD:** Sprint JSON M1-M4 closed `passes: true`; final commit

### Friday 2026-06-12

- 4.3 sprint is DONE. Polish backstop only — fix-it buffer for any issues surfaced overnight before the demo
- 4.2 M4 CLI smoke + rehearsal runs against polished shell (sister sprint owns Fri)

## Quality gates (after each milestone)

```bash
npm run quality:check:fast    # lint + tsc + auth-fetch
```

End of sprint:
```bash
cd frontend && npm run quality:check    # full incl. tests + build
make demo-verify                         # smoke harness from 4.2
```

## Cut-line (if Thursday runs hot)

**First to drop:** M4 race-guard audit (audit alone — keep workspace-into-tab; that's the iframe-stability fix that matters)
**Next:** workspace-into-tab + race-guard entirely → M1+M2+M3 alone ship the visible polish (sidebar sections + animated badges + SignInRequired + InContextBadge are the user-facing wins)
**Last resort:** Drop M2 EmptyTab + emptyBody field — ship Workbench polish patches only (badge/underline/fade-in/width). EmptyTab is v6.5 polish; nobody opens an empty tab in the demo flow

## Open Q's locked (per design doc)

- **Q1 sidebar section order** → Sessions / Documents / History
- **Q2 audit chip-row slot** → skip this sprint (no zero-cost wiring; 4.1 M3 owns when it ports)
- **Q3 `emptyBody` field on WorkbenchTab** → ADD (additive, backwards-compat)
- **Q4 workspace-into-tab default** → keep flex-sibling WorkspaceSurfaceRegion as default (backwards compat)

## Risks

| Risk | Mitigation | Severity |
|---|---|---|
| Aitana ChatShell state shape differs from gde-ap-agent (var names, computation order) | M1 first task: grep existing page.tsx; adapt port | low |
| Workbench test snapshots need regeneration after class string changes | `vitest --update` with eyeball review; chrome-devtools confirmation post-deploy | low |
| Parallel-track collision with 4.2 M4 | Zero file overlap (frontend vs CLI/backend); coordinate via git push order | low |
| chrome-devtools MCP locked at verify time | Curl + HTML grep fallback (proven during M3.5 today); Cloud Build smoke-deployed step is the gate | low |

## Execution

Per Mark's request 2026-06-09: continuous execution through to M4 verify + commit. Pause only on quality-gate failure. Sprint-executor handles loop.
