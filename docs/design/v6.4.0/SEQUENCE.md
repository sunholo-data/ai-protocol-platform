# v6.4.0 Build Sequence

**Gate:** v6.3.0 substantially complete (rag-document-corpus 3.1 ✅ and client-tenant-management 3.2 ✅, both shipped 2026-06-01/02). ACTION-TRIGGER M1–M3 (write-and-run + `useActionDrivenAgent` + `aiplatform sessions trigger-action` CLI) shipped 2026-06-08, supplying the action-driven hook that the v6.4.0 workbenches consume.

**Status as of 2026-06-08:** Two docs planned. 4.1 opens v6.4.0 with fork-tested visible polish previously downstream-only; 4.2 instantiates that polish for the Acme Energy commercial demo this week (proves the multi-tenant thesis without forking).

**Theme:** Stage-grade walkthrough for Aitana. The protocol plumbing the forks pushed back is upstream (v6.2.0 + v6.3.0 + G37–G46 + ACTION-TRIGGER). The **visible demo surface** that makes those forks compelling on stage — landing narrative, tech architecture page, audit/inspector pane, workspace primitives, workbench artefacts — is still fork-only. v6.4.0 ports the demo-impressive subset back, generalised so the next `aitana-template-publish` run carries it into the public template.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 4.1 | [fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) | **P1** | ~7.5d (1 sprint) | multi-surface (2.9 ✅), surface-context (2.10 ✅), ACTION-TRIGGER (2026-06-08 ✅), StaticArtefactFrame (G35 ✅), artefact render hook (2.13 ✅), branding.ts (✅) | **Opens v6.4.0.** Ports the gde-ap-agent audit / landing / tech narrative + the CPH AIPLA workspace primitives upstream. Adds 3 Aitana-themed consultancy demo skills (ROI calculator, agent-stack visualiser, vendor-comparison validator). Net axiom score +8 — strongest hit on EARNED TRUST (+2) and PROTOCOL OVER CUSTOM (+2). New `/tech` route + `aiplatform demo` CLI subcommands. Demo target: Croatia WebSummerCamp July 2026 + Aitana sales conversations this week. |
| 4.2 | [multi-tenant-demo-readiness.md](multi-tenant-demo-readiness.md) | **P0** | ~4d (Tue–Fri 2026-06-09 → 2026-06-12) | client-tenant-management (v6.3.0 ✅), branding.ts (✅), multi-surface (2.9 ✅), 4.1 M1+M4 (parallel — see Parallel-track risk) | **Demo this week (Fri 2026-06-12).** The existing `platform-frontend` Cloud Run service IS the Acme Energy deployment — per-deployment branding via `NEXT_PUBLIC_BRAND_*` env vars (Sunholo defaults stay in `branding.ts` for the public template). M1–M3.5 shipped 2026-06-08/09 (branding, PPA skills, workbench centerpiece, ONE-themed landing live). ONE PPA expertise skill + ONE doc-compare workbench skill (centerpiece) + `extract_ppa_clauses` / `compare_ppa_contracts` / `entsoe_day_ahead_prices` ADK FunctionTools + new `SideBySideDocViewer` workspace component + `PpaClauses` typed schema. Proves "fork-by-config" deployment can match a code fork on demo quality. Net axiom score +14 — strongest hits on INSTANT FEEL (+2), EARNED TRUST (+2), SKILLS-NOT-FEATURES (+2), PROTOCOL OVER CUSTOM (+2). No new runtime branding endpoints; `aiplatform client set --enabled-skills` + `aiplatform tenant probe` + `aiplatform docs extract-clauses` + `aiplatform demo verify`. |
| 4.3 | [internal-app-shell-port.md](internal-app-shell-port.md) | **P1** | ~3d (Wed–Thu 2026-06-10 → 2026-06-11, parallel with 4.2 M4) | v6.2.0 2.9/2.10/2.13 (✅), ACTION-TRIGGER (✅), M3.5 (✅), existing `Workbench`/`SkillSessionPanel`/`DocumentHistoryPanel`/`SurfaceRegistryProvider`/`useStableThreadId` (✅) | **Ports the chat-shell architecture from gde-ap-agent (1795 LOC chat page) into Aitana (627 LOC), keeping AP-pipeline-specific content fork-side.** Six compositional patterns: (1) multi-section collapsible sidebar (`SidebarSection` `<details>` per Sessions/Documents/History), (2) auto-collapse sidebar on first user message, (3) Workbench badge halo + active underline animation + tab body fade-in + 4-breakpoint width defaults (520/640/760/860px), (4) `SignInRequired` panel that stays on the chat URL (replaces silent `router.replace("/")`), (5) `InContextBadge` "Will process: file.pdf" caption above input, (6) contextual `EmptyTab` + workspace-into-Workbench-tab mount (iframe-stability fix). Net axiom score +8 — strongest hits on GRACEFUL DEGRADATION (+2) and PROTOCOL OVER CUSTOM (+2). Zero new protocols, zero backend changes. Internal-app polish makes any v6 chat session demo-quality regardless of skill — Fri 2026-06-12 ONE demo runs against a polished shell. |
| 4.4 | [skill-driven-shell-modes.md](skill-driven-shell-modes.md) | **P1** | ~4d (Mon–Thu 2026-06-15 → 2026-06-18, post-demo) | 4.3 ChatShell primitives (must land first), 4.2 M3 SideBySideDocViewer/KeyDifferencesPanel (✅), v6.2.0 2.9/2.10 (✅), v6.0.0 skills-data-model (✅) | **Extends the "agent drives the UI" protocol pattern from surface-level to page-level.** Adds `SkillConfig.shell` schema (`mode: chat-primary \| doc-compare \| workbench-primary \| custom`) + `ShellRouter` page-level dispatcher + two new shell components (`DocCompareShell` for doc-comparison apps; `WorkbenchShell` for workbench-primary skills). `chat-primary` stays the default — existing skills unchanged. `one-doc-compare` flipped to `doc-compare` mode so SideBySideDocViewer fills the viewport with chat as a right drawer. Unlocks downstream doc-comparison forks without bypassing the platform shell. All 4.3 primitives (SidebarSection, SignInRequired, InContextBadge, EmptyTab) reused across shells. Net axiom score +10 — strongest hits on SKILLS-NOT-FEATURES (+2, one config field selects the shell), GRACEFUL DEGRADATION (+2, unknown modes fall back to chat-primary), PROTOCOL OVER CUSTOM (+2, agent skill metadata is the configuration). `shell.mode` span attribute for Cloud Trace grouping. |
| 4.5 | [skill-onboarding.md](skill-onboarding.md) | **P1** | ~3d (Wed–Fri 2026-06-10 → 2026-06-12, parallel with 4.2 M4 + 4.3) | 4.3 INTERNAL-SHELL Workbench/Sidebar primitives (✅), v6.2.0 2.9/2.10 (✅), v6.3.0 3.2 client-tenant-management (✅), ACTION-TRIGGER (✅), existing `SkillConfig.initialMessage` (✅) | **Closes onboarding friction surfaced during 2026-06-09 local UX review.** Adds `SkillConfig.welcome` block (`intro_message`, `example_documents[]`, `bucket_browser`) — sibling of 4.4's `SkillConfig.shell`, composes orthogonally. Five surfaces: (1) fresh-chat synthetic assistant intro bubble (client-only, "Assistant intro — not stored" caption), (2) `SkillExamplesPicker` replacing Workbench Workspace EmptyTab when examples set, (3) `GCSFileBrowser` port from gde-ap-agent in sidebar's 3rd section when bucket_browser set, (4) backend `/api/buckets/{name}/list` SA-proxied bucket-list endpoint, (5) documented ACTION-TRIGGER prompt-body convention so skills can declare interactive workbench widgets. Examples-bucket policy LOCKED: cross-deploy `gs://aitana-examples-public/ppa/` + per-deploy tenant buckets. one-ppa-expert + one-doc-compare ship with full welcome blocks; one-doc-compare gets per-diff "Compare clauses" interactive buttons that drive `surface-action` events. Net axiom score +8 — strongest hits on SKILLS-NOT-FEATURES (+2, welcome is per-skill config not platform code), GRACEFUL DEGRADATION (+2, every field optional/nullable). CLI: `aiplatform examples list/push`, `aiplatform bucket browse`. |
| 4.5.1 | [document-import-by-reference.md](document-import-by-reference.md) | **P0** | ~0.5d (Thu 2026-06-11, pre-demo) | 4.5 M2 SkillExamplesPicker (✅) + M4 GCSFileBrowser (✅), existing `_run_parse` / `_store_document` pipeline ([upload.py:107/141](../../../backend/tools/documents/upload.py#L107)), Firestore `parsed_documents` collection (✅) | **Completes 4.5 — clicks on examples/bucket files now actually parse instead of firing a synthetic chat-message hack.** New `POST /api/documents/import-by-reference` route reuses the upload pipeline's `_run_parse` + `_store_document` internals (skipping the file→GCS step since the file already lives in GCS). Dedup by `(userId, sourceUrl)` short-circuits re-clicks via existing in-memory parse cache + Firestore. Frontend rewires picker `onPickExample` ([page.tsx:796](../../../frontend/src/app/chat/[...path]/page.tsx#L796)) + GCSFileBrowser `onPick` ([page.tsx:660](../../../frontend/src/app/chat/[...path]/page.tsx#L660)) to call the endpoint and mount via existing `onDocumentParsed`. Zero new parsers, zero new Firestore collections, zero new protocols. Net axiom score +7 — strongest hits on INSTANT FEEL (+1, re-clicks ≤200ms), THIN-CLIENT/FAT-PROTOCOL (+1, removes the frontend chat-message hack), API FIRST (+1, channel-agnostic). New CLI: `aiplatform docs import-from-bucket --bucket X --object Y --skill-id Z`. Demo gate. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 4.1 | [fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) | Planned 2026-06-08 |
| 4.2 | [multi-tenant-demo-readiness.md](multi-tenant-demo-readiness.md) | M1+M2+M3+M3.5 shipped 2026-06-08/09; M4 pending |
| 4.3 | [internal-app-shell-port.md](internal-app-shell-port.md) | Planned 2026-06-09 |
| 4.4 | [skill-driven-shell-modes.md](skill-driven-shell-modes.md) | Planned 2026-06-09 (post-demo, starts 2026-06-15) |
| 4.5 | [skill-onboarding.md](skill-onboarding.md) | Planned 2026-06-09 (parallel with 4.2 M4 + 4.3, ships pre-demo) |
| 4.5.1 | [document-import-by-reference.md](document-import-by-reference.md) | Planned 2026-06-10 (completes 4.5 picker/browser UX — demo gate, ships Thu pre-demo) |

Estimated calendar: 4.1 ~1 week single-dev (M1 1d → M2 2d → M3 2d → M4 2d → M5 0.5d). M1, M2, M3 can run in any order; M4 requires M1 (workspace primitives) + M3 (Audit pane wired so the Vendor Validator's sub-agent surfaces).

4.2 runs **in parallel** with 4.1 against a fixed Friday 2026-06-12 demo deadline (Tue–Fri, 4 days: M1 branding 1d → M2 PPA skill + corpus 1d → M3 ENTSO-E tool 1d → M4 demo wiring + rehearsal 0.5d + 2h fix-it buffer). 4.2 instantiates 4.1's workspace primitives + workbench artefacts with ONE-specific content — if 4.1 M1 or M4 has not landed by the time 4.2 needs it, 4.2 ports the components inline (low-merge-risk; 4.1 rebases on top).

4.3 runs **in parallel with 4.2 M4** Wed–Thu 2026-06-10/11 (frontend-only; 4.2 M4 is CLI + backend smoke, no merge collision). M1 SidebarShell 1d → M2 Workbench polish 0.5d → M3 chat-shell ergonomics 0.5d → M4 workspace-into-tab + verify 0.5d. Lands the chat-shell polish so Friday demo runs against both the M3.5 landing AND the polished internal app.

---

## What ships in v6.4.0

**From 4.1 (fork-visual-demo-pullback) — generic upstream polish:**
- **Workspace primitives** — `WorkspaceShell`, `WorkspaceDivider`, `ProgressChecklist`, `ProblemStatementCard`, `ChatRevealTab`. Generalised from CPH AIPLA; ready for any skill to mount a workbench on the multi-surface API.
- **Landing narrative** — `Hero`, `DemoSteps`, `DemoVideo`, `ProtocolStripe`. RSC-rendered. Driven by `BRANDING.demo.*` so every fork rebrands by editing `branding.ts`.
- **Tech / architecture page** — new `/tech` route with animated `ArchitectureDiagram` + per-protocol `ProtocolDiagram`. Data-driven from `branding.tech.architecture`; light/dark mode without coordinate drift.
- **Audit / Inspector pane** — `AuditViewBar` + `InspectorPanel` + `SpecialistChip` + `useSpecialistInvocations` hook. Generalised from gde-ap-agent (was AP-pipeline-coupled). Surfaces sub-agent handoffs in real time during chat. Optional `audit_specialists` field on `SkillConfig` opts a skill in.
- **3 Aitana consultancy demo skills** — `roi-calculator`, `agent-stack-visualiser`, `vendor-comparison-validator`. Each demonstrates one protocol concept (A2UI surface + surface-context, ACTION-TRIGGER, MCP App + Audit pane integration).
- **`aiplatform demo` CLI subcommand group** — `demo open` (browser launch), `demo verify` (12-line smoke harness).

**From 4.2 (ONE Demo Readiness — This Deployment Is Acme Energy) — Fri 2026-06-12 demo:**
- **Per-deployment branding** — `branding.ts` extended to read `NEXT_PUBLIC_BRAND_*` env vars with **Sunholo defaults preserved**. The existing `platform-frontend` Cloud Run service IS the ONE deployment; Cloud Build substitutions inject ONE strings + logo at build time. Zero runtime overhead. No `useTenantBranding` hook, no `/api/branding/{domain}` endpoint, no `?tenant=` query bypass — explicitly cut 2026-06-09.
- **Per-user skill filter (defence-in-depth)** — `ClientConfig.enabled_skills: list[str] | None` server-side filter on `GET /api/skills`. Null = all skills (backwards compat).
- **`one-ppa-expert` skill** — chat skill with PPA / PtX / BESS vocabulary (ported verbatim from v5 `prompt.txt`), RAG over the real ONE corpus at `gs://multivac-acme-energy-bucket` (247 indexed PPA files via Vertex AI Search datastore `one_generic`).
- **`one-doc-compare` skill (workbench centerpiece)** — side-by-side comparison of two PPA contracts with agent-driven "key differences" reasoning. The high-value ONE-consultant workflow.
- **`SideBySideDocViewer` workspace component** — synchronized scroll + block-level diff highlights between two parsed contracts; Key Differences panel renders `commercial_implication` per diff; click-to-explain in chat with both source `block_id` citations.
- **`PpaClauses` typed schema + `extract_ppa_clauses` / `compare_ppa_contracts` / `entsoe_day_ahead_prices` ADK FunctionTools** — single Pydantic schema drives Clause Extraction Card + Key Differences Summary + diff output (define once, reuse three ways). ENTSO-E returns rows + `source_uri` cite to BigQuery `your-entsoe-project.entsoe.*` for every numeric claim.
- **A2UI surfaces** — PPA Contract Summary Card, Clause Extraction Card (EARNED TRUST centerpiece), Key Differences Summary, ENTSO-E Price Chart Card, Citation Chip — all built from existing A2UI v0.9 primitives (no new primitives invented).
- **Extended CLI** — `aiplatform client set --enabled-skills`, `aiplatform tenant probe`, `aiplatform docs extract-clauses`, `aiplatform demo verify`. No `--logo-url` / `--hero-title` flags (branding is per-deployment env vars, not per-domain runtime config).

**From 4.5.1 (document-import-by-reference) — closes the 4.5 picker/browser UX gap:**
- **`POST /api/documents/import-by-reference`** — new sibling route that reuses the existing `_run_parse` + `_store_document` pipeline. Skips the file→GCS step (file already lives in GCS). Dedup by `(userId, sourceUrl)` returns parsed records ≤200ms via the existing in-memory parse cache + Firestore `parsed_documents` collection. Zero new parsers, zero schema changes.
- **Picker + GCSFileBrowser rewires** — both surfaces stop firing synthetic chat messages; instead they POST to the import endpoint and mount the returned doc in the workbench through the same `onDocumentParsed` path uploads use.
- **`aiplatform docs import-from-bucket --bucket X --object Y --skill-id Z`** — new CLI subcommand.

## Dependency Graph

```
v6.3.0 complete (3.1 ✅ + 3.2 ✅)
   │
   └── ACTION-TRIGGER M1-M3 (2026-06-08 ✅)
            │
            ├──► fork-visual-demo-pullback (4.1)
            │        │
            │        ├── multi-surface-rendering (v6.2.0 2.9 ✅)        — workspace surface mount
            │        ├── a2ui-surface-context (v6.2.0 2.10 ✅)          — surface → agent state loop
            │        ├── artefact-render-hook (v6.2.0 2.13 ✅)          — defence-in-depth for workbenches
            │        ├── StaticArtefactFrame (G35 ✅)                    — spec-compliant artefact rendering
            │        ├── branding.ts (✅, frontend)                      — single-file fork rebrand point
            │        └── useActionDrivenAgent (2026-06-08 ✅)            — workbench buttons drive the agent
            │
            └──► multi-tenant-demo-readiness (4.2)  [parallel — fixed Fri 2026-06-12 deadline]
                     │
                     ├── client-tenant-management (v6.3.0 3.2 ✅)        — clients/{domain} CRUD + bucket resolution
                     ├── branding.ts (✅, frontend)                      — fallback for tenant branding hook
                     ├── multi-surface-rendering (v6.2.0 2.9 ✅)         — workspace artefact mount
                     ├── a2ui-surface-context (v6.2.0 2.10 ✅)           — workbench → agent state loop
                     └── 4.1 M1 workspace primitives + M4 workbench artefacts (parallel — fallback: port inline if 4.1 hasn't landed)

skill-onboarding (4.5 ✅, shipped 2026-06-10)
   │
   └──► document-import-by-reference (4.5.1)  [Thu pre-demo gate]
            │
            ├── 4.5 M2 SkillExamplesPicker (✅)                          — surfaces example_documents to click
            ├── 4.5 M4 GCSFileBrowser (✅)                               — surfaces bucket_browser files to click
            ├── existing _run_parse / _store_document (✅, upload.py)    — same parse + cache + dedup as user uploads
            └── existing parsed_documents Firestore collection (✅)      — durable cross-session cache
```

## Next: v6.5.0

Not yet planned. Candidates carried over from v6.3.0 SEQUENCE: shared/team RAG corpora (per-skill), real-time collaborative editing, voice (Gemini Live), skill marketplace. Plus from v6.4.0 deferrals: **teacher / operator-dashboard pattern** (port from CPH `teacher/` directory — `ClassVoiceSettingsPanel` + `insights/`), and template publication of the v6.4.0 visual layer via `aitana-template-publish`.
