# Sprint Plan: ONE-DEMO — Acme Energy Demo Readiness

## Summary

Land ONE-branded deployment + PPA expertise skill + side-by-side contract comparison workbench
on the existing `platform-frontend` Cloud Run service in 4 days, ready for the commercial demo
Fri 2026-06-12.

**Duration:** 4 days (Tue 2026-06-09 → Fri 2026-06-12)
**Scope:** Fullstack — frontend branding + workspace components + backend skills + ADK tools
**Dependencies:** v6.3.0 ✅ (client-tenant-management, branding.ts), v6.2.0 ✅ (multi-surface 2.9, surface-context 2.10, artefact render hook 2.13)
**Risk Level:** Medium-High (fixed Friday deadline; M3 centerpiece is genuinely new work; 4.1 not landed)
**Design Doc:** [multi-tenant-demo-readiness.md](multi-tenant-demo-readiness.md)
**Sprint ID:** ONE-DEMO

## Current Status Analysis

### Recent Velocity (last 14 days)

- **52 commits**, **19,816 insertions** / **727 deletions** across 159 files
- Recent sprints — **ACTION-TRIGGER** M1-M3 shipped in 3 days (one milestone per day, write-and-run + useActionDrivenAgent + sessions trigger-action CLI). **A2A-DOCS** M1-M4 + **A2A-INVOKE** M1-M3 each multi-day. Velocity is consistently ~600-900 LOC/day across fullstack milestones with tests.
- **Estimated capacity for this sprint:** ~3,000 LOC across frontend + backend + tests + CLI. The proposed plan totals ~2,800 LOC. Tight but achievable at recent velocity.

### Existing Implementation We Build On

- `frontend/src/lib/branding.ts` already reads `process.env.NEXT_PUBLIC_*` for `CITATION_SCHEME` + `TRANSPORT_FIELD` (lines 26-36). M1 extends the same `||`-fallback pattern to the `BRANDING` object.
- `cloudbuild.yaml` already has `--build-arg NEXT_PUBLIC_*` Docker build-arg flow at line 90 + terraform-managed substitutions. M1's substitutions slot in mechanically.
- `frontend/src/components/document/BlocksRenderer.tsx` already renders AILANG-parse block structures — `SideBySideDocViewer` wraps two instances.
- `backend/tools/documents/ailang_parse.py` already parses GCS files to blocks; the `extract_ppa_clauses` tool consumes the existing output.
- `backend/tools/structured_extraction.py` already wires ADK output_schema; the new clause-extraction tool follows the same pattern.
- `backend/db/clients.py` `ClientConfig` already supports `documents_bucket` + `display_name`; M1 adds one more nullable field.
- v6.3.0 `aiplatform client set` CLI exists; M1 adds one flag.

### Critical Gap Discovered During Planning

- **`fork-visual-demo-pullback` (v6.4.0 4.1) has NOT shipped.** No `WorkspaceShell`, `WorkspaceDivider`, `InspectorPanel`, `AuditViewBar`, or `RoiCalculatorWorkbench` exist in the codebase.
- **Impact:** M3 must inline-port the minimum workspace primitives it needs (`WorkspaceShell` + `WorkspaceDivider`, ~200 LOC). 4.1's heavier surfaces (Audit pane, ProgressChecklist, Hero/ProtocolStripe, `/tech` route, the 3 generic demo skills) stay deferred.
- **Mitigation:** the inline-port budget is baked into M3 estimates below. If 4.1 lands mid-sprint, our inline-ports rebase against 4.1's generalised versions.
- **Casualty:** PPA Cost Estimator + PtX Tech Comparator (which were "ONE-config of 4.1's workbenches") become **stretch goals only**. They're flagged Non-Critical in M3. The doc-compare workbench alone carries the demo.

## Proposed Milestones

### Milestone M1: Per-Deployment Branding + Skill Filter

**Scope:** fullstack
**Goal:** Visitors to the existing `platform-frontend` Cloud Run URL see Acme Energy branding from first paint. ONE-domain users see only the 3 ONE-enabled skills.
**Estimated:** ~140 LOC implementation + ~80 LOC tests = ~220 LOC
**Duration:** 0.3 day (Tue AM)

**Tasks:**
- [ ] **Hour 1 — Verify build path.** Trace how `_MCP_SANDBOX_URL` (line 21-22 + line 90 of cloudbuild.yaml) flows through `--build-arg` into the Next.js build. Confirm `process.env.NEXT_PUBLIC_*` is readable at `next build` time, not just runtime. This is the highest-risk M1 step — failure here forces a different injection path. (~0 LOC, pure investigation; mitigates the "Cloud Build substitution path" risk from the design doc)
- [ ] Convert `<local-path>` (225x225 JPG) to SVG (Inkscape trace or treat as raster — pragmatic for the demo) and commit as `frontend/public/images/logo/acmeenergy-logo.svg` (~5 LOC if SVG wrapper around raster)
- [ ] Extend `frontend/src/lib/branding.ts` — `BRANDING` object reads `NEXT_PUBLIC_BRAND_*` with current Sunholo values as `||` fallbacks (~15 LOC delta)
- [ ] Add 8 `_BRAND_*` substitutions to `cloudbuild.yaml` substitutions block (~10 LOC)
- [ ] Add 8 `--build-arg NEXT_PUBLIC_BRAND_*=${_BRAND_*}` lines to the existing `--build-arg` block at line 90+ (~15 LOC)
- [ ] Frontend tests — `branding.test.ts`: Sunholo defaults when env vars unset; ONE values when set (~40 LOC, 4 tests)
- [ ] Backend — extend `backend/db/clients.py` `ClientConfig` with `enabled_skills: list[str] | None = None` (~5 LOC)
- [ ] Backend — extend `backend/skills/routes.py` skill-list endpoint to filter by `user.tenant.enabled_skills` server-side; null = all skills (existing behaviour) (~25 LOC delta)
- [ ] Backend tests — `test_skill_list_tenant_filter.py`: admin sees all; ONE user sees filtered; unmapped sees all (~35 LOC, 3 tests)
- [ ] CLI — extend `aiplatform client set` in `cli/aiplatform/commands/client.py` with `--enabled-skills <slugs>` flag (~15 LOC + 1 test, ~25 LOC test)
- [ ] **Manual verification.** Push to `dev`; wait for Cloud Build; chrome-devtools MCP visits the live `platform-frontend-...run.app` URL and confirms ONE branding renders (logo + appName + tagline visible, no Sunholo strings). Run `aiplatform client set acme-energy.example --enabled-skills one-ppa-expert,one-doc-compare,general-assistant` (these skills don't exist yet — set the filter pointing forward).

**Files to Create/Modify:**
- `frontend/public/images/logo/acmeenergy-logo.svg` (new, ~5 LOC SVG)
- `frontend/src/lib/branding.ts` (modify, ~15 LOC delta)
- `frontend/src/lib/__tests__/branding.test.ts` (new, ~40 LOC)
- `cloudbuild.yaml` (modify, ~25 LOC delta)
- `backend/db/clients.py` (modify, ~5 LOC)
- `backend/skills/routes.py` (modify, ~25 LOC)
- `backend/tests/api_tests/test_skill_list_tenant_filter.py` (new, ~35 LOC)
- `cli/aiplatform/commands/client.py` (modify, ~15 LOC)
- `cli/tests/test_cli_client_enabled_skills.py` (new, ~25 LOC)

**Acceptance Criteria:**
- [ ] Live deployed URL renders "Acme Energy" appName + ONE logo + ONE tagline; Sunholo defaults still apply if env vars unset (regression check by temporarily clearing one)
- [ ] `branding.test.ts` 4 tests pass
- [ ] `test_skill_list_tenant_filter.py` 3 tests pass
- [ ] `aiplatform client set acme-energy.example --enabled-skills ...` round-trips and shows in `aiplatform client get`
- [ ] `npm run quality:check:fast` green
- [ ] `cd backend && make lint && make test-fast` green

**Risks:**
- *Cloud Build substitution path may not flow `NEXT_PUBLIC_*` into the Next.js build context cleanly* — Mitigation: Hour 1 investigation. Fallback: set vars directly on Cloud Run service env + use Next.js's runtime config (not build-time bake). Slightly slower first paint but acceptable for the demo.
- *SVG conversion of v5 JPG logo may look bad at large sizes* — Mitigation: ship raster-in-SVG for Tue; if quality is poor, request real vector from ONE before Fri or use a placeholder name-only logo.

---

### Milestone M2: ONE PPA Skill + Corpus + Clause Extraction + ENTSO-E

**Scope:** fullstack
**Goal:** `one-ppa-expert` skill answers PPA vocabulary questions with cited extracts from ONE's real corpus at `gs://multivac-acme-energy-bucket` — via `ai_search` (existing Vertex AI Search datastore `one_generic`) for semantic queries AND direct GCS reads for known docs. `extract_ppa_clauses(doc_id)` returns typed `PpaClauses` with `block_id` citations rendered as a `ClauseExtractionCard`; ENTSO-E day-ahead prices queryable from the agent.
**Estimated:** ~720 LOC implementation + ~340 LOC tests = ~1,060 LOC
**Duration:** 1 day (Wed)

**Tasks:**

*Schema + extraction tool (~3h, ~370 LOC):*
- [ ] `backend/tools/schemas/ppa_clauses.py` — define `ClauseExtraction`, `PpaClauses`, `PpaComparison`, `ClauseDifference` Pydantic models. Make sure `model_config = ConfigDict(populate_by_name=True)` for ADK structured-output compat. (~120 LOC)
- [ ] `backend/tools/extract_ppa_clauses.py` — ADK FunctionTool. Loads doc via existing `get_document_content` → AILANG block list → ADK structured-output call (Claude Sonnet, `output_schema=PpaClauses`) → returns typed `PpaClauses`. Use `backend/tools/structured_extraction.py` as the wire pattern. (~200 LOC)
- [ ] `backend/tests/tools/test_extract_ppa_clauses.py` — happy path with a fixture PPA doc + missing-block path + schema-conformance assertion (~120 LOC, 5 tests)
- [ ] Register tool in skill's allowed-tools list

*ENTSO-E + Cross-project Storage + Discovery Engine IAM (~2.5h, ~190 LOC):*
- [ ] **3 cross-project IAM grants** (single gcloud session):
  - On `your-entsoe-project`: `roles/bigquery.dataViewer` to `sa-platform@your-project-id.iam.gserviceaccount.com`
  - On `multivac-acme-energy`: `roles/storage.objectViewer` on bucket `gs://multivac-acme-energy-bucket` to same SA
  - On `multivac-acme-energy`: `roles/discoveryengine.viewer` at project level to same SA
  All 3 are cross-project (from `your-project-id`). ~10 min total. Track terraform follow-up per `feedback_no_manual_iam_grants`. Gcloud acceptable for sprint speed; add to terraform as TODO.
- [ ] `backend/tools/entsoe_query.py` — single function `entsoe_day_ahead_prices(bidding_zone, start_date, end_date)`. Typed parameterized SQL template, returns `{rows: [{ts, price_eur_mwh}], source_uri: "bq://your-entsoe-project.entsoe.day_ahead_prices?filter=..."}`. Errors as structured strings (Axiom 5). (~120 LOC)
- [ ] `backend/tests/tools/test_entsoe_query.py` — happy path (BQ mocked), error path, source_uri format assertion (~70 LOC, 3 tests). Integration test gated on `ENTSOE_INTEGRATION_TEST=1` env var (separate ~30 LOC, not in CI).

*PPA expertise skill + wire to real corpus (~2h, ~250 LOC):*
- [ ] `backend/skills/templates/one-ppa-expert/SKILL.md` — port v5 `<local-path>` body verbatim into the prompt section. Frontmatter: `name: one-ppa-expert`, `display_name: Contract Expert`, `tags: [ppa, energy, acme-energy.example]`, `model: gemini-2.5-flash`, `tools: [ai_search, list_documents, get_document_content, extract_ppa_clauses, entsoe_day_ahead_prices, google_search]`, `toolConfigs.ai_search.datastore: <ONE-vertex-search-datastore-id>` (likely `one_generic` per v5 vac_config; canonical resource path needs confirming via Console), `initial_message: "PPA, PtX, BESS — what would you like to analyse?"` (~150 LOC including prompt body)
- [ ] `backend/tests/skills/test_one_ppa_expert.py` — skill loads via `load_skill_from_dir`, validates against `SkillConfig`, one happy-path message through Runner with mocked tools (~100 LOC, 3 tests)
- [ ] **~~Confirm corpus content~~ — already exists.** ONE's real bucket `gs://multivac-acme-energy-bucket` has 247 files under `PPAs/` + 821 under `documents/` + Vertex AI Search index `one_generic` running on top. No upload step. Wire-up only:
  - `aiplatform client set acme-energy.example --documents-bucket multivac-acme-energy-bucket --enabled-skills one-ppa-expert,one-doc-compare,general-assistant`
  - Cross-project IAM (see "BQ + Storage IAM" task above): `sa-platform` gets `storage.objectViewer` on `gs://multivac-acme-energy-bucket` + `discoveryengine.viewer` on `multivac-acme-energy` project
  - Find canonical Vertex AI Search resource path via GCP Console → `multivac-acme-energy` → AI Applications → Data Stores. Update SKILL.md frontmatter.
- [ ] **Pick 2 demo contracts** from `PPAs/longform/` by Wed EOD for the M3 doc-compare demo. Ideally same-template-derived (clean `block_id` alignment) with 3+ material clause differences. Confirm with ONE that pair is OK to use.
- [ ] Publish skill to Firestore via `aiplatform skill push` (uses existing v6.3.0 CLI)

*A2UI Clause Extraction Card (~2h, ~250 LOC):*
- [ ] `frontend/src/components/clause-extraction/ClauseExtractionCard.tsx` — renders `PpaClauses` as a table: clause display_name | extracted value | confidence badge (high/medium/low colour) | citation chip (`aitana://doc/{docId}/block/{blockId}`). Click chip → existing block-navigation behaviour from `BlocksRenderer`. Uses A2UI v0.9 Container + DataTable primitives via the existing artefact render hook (v6.2.0 2.13). (~180 LOC + ~70 LOC tests = 3 Vitest tests)

**Files to Create/Modify:**
- `backend/tools/schemas/ppa_clauses.py` (new, ~120 LOC)
- `backend/tools/extract_ppa_clauses.py` (new, ~200 LOC)
- `backend/tests/tools/test_extract_ppa_clauses.py` (new, ~120 LOC)
- `backend/tools/entsoe_query.py` (new, ~120 LOC)
- `backend/tests/tools/test_entsoe_query.py` (new, ~100 LOC including integration test)
- `backend/skills/templates/one-ppa-expert/SKILL.md` (new, ~150 LOC)
- `backend/tests/skills/test_one_ppa_expert.py` (new, ~100 LOC)
- `frontend/src/components/clause-extraction/ClauseExtractionCard.tsx` (new, ~180 LOC)
- `frontend/src/components/clause-extraction/__tests__/ClauseExtractionCard.test.tsx` (new, ~70 LOC)
- IAM: BigQuery grant on `your-entsoe-project` (gcloud command; track terraform follow-up)

**Acceptance Criteria:**
- [ ] `one-ppa-expert` skill loads in the marketplace for ONE users (after M1 enables the filter)
- [ ] Skill answers "what's the difference between PaP and PaN settlement?" with PPA-vocabulary-correct response
- [ ] Skill answers semantic-RAG question via `ai_search` against ONE's existing Vertex AI Search datastore (e.g. "find contracts mentioning RFNBO compliance") with cited extracts
- [ ] Skill answers direct-doc question via `list_documents`/`get_document_content` against `gs://multivac-acme-energy-bucket/PPAs/longform/` with cited extracts
- [ ] `extract_ppa_clauses(doc_id)` returns typed `PpaClauses` with `block_id` populated on every non-null clause
- [ ] `ClauseExtractionCard` renders the extracted clauses inline in chat with citation chips clickable
- [ ] `entsoe_day_ahead_prices("DK1", "2026-06-01", "2026-06-08")` returns rows + cited `bq://...` source_uri in <3s
- [ ] All 17 new tests pass; `make lint && make test-fast` green; `npm run quality:check:fast` green

**Risks:**
- *Corpus content TBD (Q2)* — Mitigation: ONE confirm by 10am Wed; fallback to public PPA templates so M2 isn't blocked.
- *Claude Sonnet structured-output extraction may produce malformed `PpaClauses` if a PPA contract has unusual structure* — Mitigation: prompt instructs "set field to null if not present"; schema has every clause field as `| None`. Test on fixture docs Wed PM.
- *BigQuery cold query latency ~1-3s may stretch on first call* — Mitigation: acceptable for demo; warm-up call from `aiplatform tenant probe` in M4.

---

### Milestone M3: Doc-Compare Workbench Centerpiece

**Scope:** fullstack
**Goal:** ONE consultant opens `one-doc-compare`, picks two PPA contracts, sees side-by-side rendering with block-level diff highlights, KeyDifferencesPanel populates with `commercial_implication` per diff, clicking a diff explains it in chat with both source `block_id` citations.
**Estimated:** ~970 LOC implementation + ~450 LOC tests = ~1,420 LOC
**Duration:** 1.7 days (Thu full + Fri morning if M1 finished by Tue noon, freeing buffer)

**Tasks:**

*Backend — compare tool + skill (~3h, ~370 LOC):*
- [ ] `backend/tools/compare_ppa_contracts.py` — ADK FunctionTool. Takes `(left_doc_id, right_doc_id)`. Calls `extract_ppa_clauses` twice in parallel (use existing async pattern). Runs structured comparison (Claude Sonnet, `output_schema=PpaComparison`). Every `ClauseDifference` populated with `commercial_implication` (1-2 sentence agent-generated). (~230 LOC + ~130 LOC tests with mocked extract output, 5 tests)
- [ ] `backend/skills/templates/one-doc-compare/SKILL.md` — orchestrator skill. Frontmatter: `tools: [list_documents, extract_ppa_clauses, compare_ppa_contracts, entsoe_day_ahead_prices]`, `model: gemini-2.5-flash` for chat / Claude Sonnet via tool model-tier escalation. Prompt body: comparison rubric covering definitions / settlement / price / term / termination / change-of-law / force-majeure / indemnity. (~130 LOC including prompt body + ~70 LOC tests, 3 tests)
- [ ] Publish skill to Firestore

*Frontend — inline workspace primitives from 4.1 (~3h, ~280 LOC):*

Since 4.1 hasn't shipped, port the minimum-viable workspace primitives inline. These are designed
to be merge-friendly when 4.1 lands.

- [ ] `frontend/src/components/workspace/WorkspaceShell.tsx` — split-pane layout container (left: chat, right: workspace artefact). ~120 LOC. Source: 4.1's M1 spec in `fork-visual-demo-pullback.md` — port without the Danish copy fallback CPH has.
- [ ] `frontend/src/components/workspace/WorkspaceDivider.tsx` — resizable column divider with drag handle. ~100 LOC. Port from 4.1 spec verbatim.
- [ ] `frontend/src/components/workspace/__tests__/` — basic mount + drag-behaviour tests (~60 LOC, 4 tests)

*Frontend — SideBySideDocViewer (~4h, ~520 LOC):*
- [ ] `frontend/src/components/workspace/SideBySideDocViewer.tsx` — composes `WorkspaceShell` + `WorkspaceDivider` + 2x `BlocksRenderer` (existing). Block-level diff overlay: align by AILANG `block_id` when present, fall back to text-similarity matching (jaccard over tokenized text, threshold 0.7) for unmatched blocks. Diff classifications: added (only-in-right) / removed (only-in-left) / modified (similar but not identical). Click handler on any highlighted span emits `surface-action` with `{type: "diff-clicked", left_block_id, right_block_id, clause_name}`. (~400 LOC + ~120 LOC tests, 5 tests covering align algorithm + sync scroll + click handler + diff classification + render)
- [ ] `frontend/src/components/workspace/useSyncedScroll.ts` — hook syncing scroll position between two refs. ~50 LOC + ~30 LOC tests, 2 tests
- [ ] `frontend/src/lib/diff/blockAlign.ts` — pure-function block alignment algorithm (block_id-first, text-similarity fallback). Easier to unit-test than UI. ~120 LOC + ~80 LOC tests, 6 tests covering all paths.

*Frontend — KeyDifferencesPanel A2UI artefact (~2h, ~280 LOC):*
- [ ] `frontend/src/components/workspace/KeyDifferencesPanel.tsx` — renders `PpaComparison.differences` as ordered list with severity badges (material/moderate/cosmetic). Expand row → reveal `left_excerpt` + `right_excerpt` + `commercial_implication`. Click → emit `surface-action`. (~180 LOC + ~100 LOC tests, 3 tests)
- [ ] Skill agent emits `KeyDifferencesPanel` as A2UI artefact targeting the `workspace` named surface after `compare_ppa_contracts` returns. Wires via existing artefact render hook (v6.2.0 2.13).

*Workspace mount (~1h, ~50 LOC):*
- [ ] `one-doc-compare` skill's agent emits A2UI artefact at start of every turn that has two doc IDs in context. Surface mounts via v6.2.0 2.9 multi-surface API; persists across turns.

*Manual verification (~1h):*
- [ ] Upload 2 fixture PPA contracts (M2 corpus)
- [ ] Open `one-doc-compare` in browser via chrome-devtools MCP (use `aitana-frontend-verify` skill)
- [ ] Verify: workbench mounts, both contracts render side-by-side, diff overlay highlights, KeyDifferencesPanel populates with commercial_implications, click a price-formula diff → agent explains in chat with both `block_id` citations, ask "what would this cost at DK1 prices?" → ENTSO-E composes with comparison

**Files to Create/Modify:**
- `backend/tools/compare_ppa_contracts.py` (new, ~230 LOC)
- `backend/tests/tools/test_compare_ppa_contracts.py` (new, ~130 LOC)
- `backend/skills/templates/one-doc-compare/SKILL.md` (new, ~130 LOC)
- `backend/tests/skills/test_one_doc_compare.py` (new, ~70 LOC)
- `frontend/src/components/workspace/WorkspaceShell.tsx` (new, ~120 LOC)
- `frontend/src/components/workspace/WorkspaceDivider.tsx` (new, ~100 LOC)
- `frontend/src/components/workspace/__tests__/WorkspaceShell.test.tsx` + `WorkspaceDivider.test.tsx` (new, ~60 LOC)
- `frontend/src/components/workspace/SideBySideDocViewer.tsx` (new, ~400 LOC)
- `frontend/src/components/workspace/__tests__/SideBySideDocViewer.test.tsx` (new, ~120 LOC)
- `frontend/src/components/workspace/useSyncedScroll.ts` (new, ~50 LOC)
- `frontend/src/lib/diff/blockAlign.ts` (new, ~120 LOC)
- `frontend/src/lib/diff/__tests__/blockAlign.test.ts` (new, ~80 LOC)
- `frontend/src/components/workspace/KeyDifferencesPanel.tsx` (new, ~180 LOC)
- `frontend/src/components/workspace/__tests__/KeyDifferencesPanel.test.tsx` (new, ~100 LOC)

**Acceptance Criteria:**
- [ ] `one-doc-compare` skill loads in marketplace for ONE users
- [ ] User can select 2 docs and the workbench opens with `SideBySideDocViewer`
- [ ] Both contracts render parsed via `BlocksRenderer` (left + right panes)
- [ ] Diff overlay highlights at the block level — added (green) / removed (red) / modified (amber)
- [ ] Synchronized scroll works between the two panes
- [ ] `KeyDifferencesPanel` populates with at least 3 differences for a real PPA contract pair
- [ ] Every diff carries `left_block_id` + `right_block_id` citation
- [ ] Every diff has `commercial_implication` text
- [ ] Click a diff → `surface-action` fires → next agent turn explains the diff
- [ ] End-to-end valuation works: ask "what would this cost at DK1?" → composes comparison + ENTSO-E
- [ ] All 23 new tests pass; `make lint && make test-fast` green; `npm run quality:check:fast` green

**Risks (HIGH — this is the demo centerpiece):**
- *Block-level diff alignment quality (Open Q7) — text-similarity fallback for unmatched blocks may produce ugly noise* — Mitigation: tune jaccard threshold during M3 PM; add a "low-confidence diff" badge so noisy diffs are visually de-emphasized; worst case for demo: pick fixture contracts that share AILANG block_ids well (extracted from same template).
- *Claude Sonnet `commercial_implication` text quality varies* — Mitigation: explicit prompt instruction "1 sentence, factual, no hedging"; eval on fixture pair Thu PM.
- *4.1 may land mid-Thursday and conflict with our inline ports* — Mitigation: keep `WorkspaceShell` + `WorkspaceDivider` as straight ports from 4.1's spec (low merge risk); if 4.1 lands first, rebase the inline ports out.
- *SideBySideDocViewer is genuinely new ~400 LOC with sync scroll + drag divider + diff overlay — historically the riskiest single component this sprint* — Mitigation: implementation order is `blockAlign` (pure function, unit-testable in isolation) → `useSyncedScroll` (hook) → `WorkspaceShell`/`Divider` → composed `SideBySideDocViewer`. Each layer ships independently testable.

---

### Milestone M4: Demo Wiring + CLI + Rehearsal

**Scope:** fullstack
**Goal:** Sprint deliverables verifiable in one `aiplatform demo verify` command; 5-minute demo flow rehearsed and de-risked.
**Estimated:** ~280 LOC implementation + ~100 LOC tests = ~380 LOC + rehearsal time
**Duration:** 0.5 day Fri + ~2h fix-it buffer + rehearsal

**Tasks:**

- [ ] `cli/aiplatform/commands/tenant.py` — `aiplatform tenant probe <domain>` prints resolved `ClientConfig`, top 10 bucket files via `gsutil ls`, enabled-skills list, sample ENTSO-E ping (warms BQ cache for demo), sample clause-extraction on a fixture doc (warms Claude cache). (~80 LOC + ~30 LOC test)
- [ ] `cli/aiplatform/commands/docs.py` — extend with `aiplatform docs extract-clauses <doc-id>` debug helper, runs `extract_ppa_clauses` standalone, prints typed JSON output. (~40 LOC + ~20 LOC test)
- [ ] `cli/aiplatform/commands/demo.py` — `aiplatform demo verify` (no `--tenant` flag — the URL IS the deployment). Asserts: live URL renders ONE branding strings in HTML; `/api/skills` returns the 3 ONE-enabled skills; ENTSO-E ping succeeds; clause extraction on a fixture returns valid `PpaClauses`. 12-line green checklist. (~120 LOC + ~50 LOC test)
- [ ] `scripts/smoke-deployed.sh` — add ONE-tenant assertions for the dev env (~20 LOC; run once to confirm green)
- [ ] `Makefile` — `make demo-verify` target wrapping `aiplatform demo verify` (~3 LOC)
- [ ] **Rehearsal (~1h)** — run the 5-minute demo flow end-to-end via chrome-devtools MCP. Take screenshots at every step. Log every UX glitch.
- [ ] **Fix-it buffer (~2h)** — burn down rehearsal-surfaced issues. If the buffer runs dry, the demo ships as-is and the issue list becomes Mon's follow-up.
- [ ] Update `docs/talks/ai-ui-protocol-stack.md` verification log — "v6.4.0 / 2026-06-12: ONE demo — per-deployment branding + clause extraction + side-by-side compare + ENTSO-E grounding all confirmed in production rehearsal"

**Files to Create/Modify:**
- `cli/aiplatform/commands/tenant.py` (new, ~80 LOC)
- `cli/aiplatform/commands/docs.py` (modify, ~40 LOC)
- `cli/aiplatform/commands/demo.py` (new, ~120 LOC)
- `cli/tests/test_cli_tenant.py` (new, ~30 LOC)
- `cli/tests/test_cli_docs_extract.py` (new, ~20 LOC)
- `cli/tests/test_cli_demo_verify.py` (new, ~50 LOC)
- `scripts/smoke-deployed.sh` (modify, ~20 LOC)
- `Makefile` (modify, ~3 LOC)
- `docs/talks/ai-ui-protocol-stack.md` (modify, ~10 LOC)

**Acceptance Criteria:**
- [ ] `aiplatform demo verify` returns 12-line green checklist
- [ ] `aiplatform tenant probe acme-energy.example` prints sensible output
- [ ] `aiplatform docs extract-clauses <fixture-doc-id>` prints valid `PpaClauses` JSON
- [ ] `make smoke-deployed dev all` stays green
- [ ] Rehearsal completes without engineering intervention (full 5-min flow Tue-fixture-quality contracts)
- [ ] All CLI tests pass

**Risks:**
- *Rehearsal surfaces > 2h of fixes* — Mitigation: pre-rehearse the riskiest step (M3 doc-compare) on Thu EOD; reserve Fri AM as M3 spillover slot before declaring M4 in flight.

---

## Day-by-Day Breakdown

### Tue 2026-06-09 — M1 day

- **Focus:** Per-deployment branding + skill filter; verify cloud-build env-var path.
- **AM (~3h):**
  - Hour 1: trace `cloudbuild.yaml` line 90 `--build-arg NEXT_PUBLIC_*` flow; verify Next.js reads it at build time
  - Convert ONE logo JPG → SVG
  - Extend `branding.ts` with env-var reads + Sunholo fallbacks
  - Add 8 `_BRAND_*` substitutions + `--build-arg` lines
  - Frontend tests for branding
- **Midday (~2h):**
  - Backend: `ClientConfig.enabled_skills` + skill-list filter + tests
  - CLI: `aiplatform client set --enabled-skills` + test
  - Local quality gates green
- **PM (~3h):**
  - Push to `dev` branch, watch Cloud Build trigger
  - chrome-devtools MCP verify ONE branding renders on the live URL
  - Run `aiplatform client set acme-energy.example --enabled-skills ...`
  - **Roll-forward into M2 if time permits** — start the `PpaClauses` schema or pre-stage the ONE corpus content question for ONE (send Slack/email)
- **Checkpoint:** Live `platform-frontend-...run.app` renders Acme Energy branding. Skill filter mechanism works (verifiable even before the skills exist by setting filter to current public skills).

### Wed 2026-06-10 — M2 day (4 parallel tracks)

- **Focus:** ONE PPA skill, document corpus, clause extraction, ENTSO-E. Heaviest day for parallel work.
- **AM (~4h):**
  - **By 10am:** confirm Q2 (corpus content) with ONE — go/no-go on real docs vs public templates
  - PPA clauses Pydantic schema (`schemas/ppa_clauses.py`)
  - `extract_ppa_clauses` FunctionTool + tests
  - In parallel: apply 3 cross-project IAM grants (BQ + Storage + Discovery Engine); find canonical Vertex Search datastore resource path via GCP Console; pick 2 demo contracts from `PPAs/longform/`
- **Midday (~2h):**
  - `one-ppa-expert/SKILL.md` (port v5 prompt + frontmatter + tools list)
  - Skill tests
  - Publish skill via `aiplatform skill push`
- **PM (~3h):**
  - BQ IAM grant (gcloud); add to terraform-pending-grants TODO
  - `entsoe_query.py` + tests (mocked + integration)
  - `ClauseExtractionCard.tsx` + tests
  - Quality gates: lint, test-fast, vitest, typecheck
  - **Smoke:** ask the deployed skill a PPA question + extract clauses + query ENTSO-E
- **Checkpoint:** Three new tools registered, skill answers PPA questions with citations, ENTSO-E returns prices. ClauseExtractionCard renders inline in chat.

### Thu 2026-06-11 — M3 day (demo centerpiece, highest risk)

- **Focus:** Side-by-side compare workbench. Build bottom-up (pure functions first, UI last).
- **AM (~3h):**
  - `blockAlign.ts` algorithm + 6 unit tests (pure function, isolatable)
  - `compare_ppa_contracts` tool + tests (mocked extract output)
  - `one-doc-compare/SKILL.md` + tests
- **Midday (~3h):**
  - `WorkspaceShell` + `WorkspaceDivider` (inline ports from 4.1 spec)
  - `useSyncedScroll` hook
  - Mount-and-drag tests
- **PM (~4h):**
  - `SideBySideDocViewer` (composes shell + divider + 2x BlocksRenderer + diff overlay)
  - `KeyDifferencesPanel` A2UI artefact
  - Wire skill agent to emit artefact on turn with 2 doc IDs
  - **End-of-day smoke:** open `one-doc-compare` in browser via chrome-devtools, verify side-by-side renders, click a diff
- **Checkpoint:** ONE consultant flow works end-to-end on a fixture pair: pick 2 docs → workbench mounts → diff visible → click → agent explains. Quality may be rough but the path is unblocked.
- **Risk gate:** if SideBySideDocViewer isn't visibly working by Thu EOD, M4 demo-rehearsal slot becomes a M3 spillover slot, and demo content de-scopes (clause extraction + ENTSO-E carry it without doc-compare).

### Fri 2026-06-12 — M4 day + DEMO

- **Focus:** Polish, CLI verifier, smoke, rehearse, demo.
- **AM (~3h):**
  - `aiplatform tenant probe` + `docs extract-clauses` + `demo verify` CLI commands + tests
  - `scripts/smoke-deployed.sh` ONE assertions
  - `make demo-verify` target
  - **Full rehearsal** — 5-min demo flow via chrome-devtools MCP, screenshots at each step
- **Midday (~2h):**
  - Burn down rehearsal-surfaced issues (the 2h fix-it buffer)
  - Update `docs/talks/ai-ui-protocol-stack.md` verification log
  - Final `aiplatform demo verify` run — must be green
- **PM:**
  - **DEMO**
- **Checkpoint:** Demo runs end-to-end on the live URL without engineering intervention.

## Quality Gates

After each milestone:
```bash
# Frontend
cd frontend && npm run quality:check:fast

# Backend
cd backend && make lint && make test-fast
```

After M4 (pre-demo):
```bash
# Full CI parity
cd frontend && npm run quality:check
cd backend && make lint && make test-fast

# Smoke against deployed dev
./scripts/smoke-deployed.sh dev all
make demo-verify
```

## Success Metrics

- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] All backend tests passing (`cd backend && make lint && make test-fast`)
- [ ] All CLI tests passing (`cd cli && uv run pytest`)
- [ ] `make demo-verify` returns green 12-line checklist
- [ ] `make smoke-deployed dev all` stays green
- [ ] Deployed URL renders Acme Energy branding for every visitor (no `?tenant=`, no auth gate)
- [ ] Zero "Sunholo" / "Aitana" strings visible in the ONE-branded UI (CI grep)
- [ ] `one-ppa-expert` answers PPA-vocabulary question with citations
- [ ] `extract_ppa_clauses(doc_id)` returns typed `PpaClauses` with block_id citations
- [ ] `one-doc-compare` renders `SideBySideDocViewer` with `KeyDifferencesPanel`
- [ ] End-to-end valuation flow (diff → ENTSO-E grounding) works on a fixture pair
- [ ] Friday demo rehearsal completes without engineering intervention

## Dependencies

**Hard blockers (must exist before sprint starts — all confirmed ✅):**
- v6.3.0 client-tenant-management (CLI + ClientConfig + bucket resolution) ✅
- v6.2.0 multi-surface rendering (workspace surface mount) ✅
- v6.2.0 a2ui-surface-context (surface → agent state loop) ✅
- v6.2.0 artefact render hook ✅
- v6.3.0 rag-document-corpus + AILANG-parse on GCS ✅
- existing `BlocksRenderer.tsx` (renders AILANG-parse blocks) ✅
- existing `cloudbuild.yaml` with `--build-arg NEXT_PUBLIC_*` pattern ✅

**Soft / parallel:**
- fork-visual-demo-pullback (4.1) — **not landed.** M3 absorbs the inline-port of `WorkspaceShell` + `WorkspaceDivider`. 4.1's Audit pane, Hero, `/tech`, ProgressChecklist, ProtocolStripe are NOT required for this demo.

**External:**
- ONE confirmation on corpus content (Q2) — needed by Wed 10am
- ONE confirmation on logo asset quality (Q from M1 risks) — can use v5 JPG-in-SVG for Tue, request real vector by Wed if quality is poor

## Open Questions (carried from design doc)

- **Q1 Demo URL** — existing `.run.app` URL or short alias under `yourcompany.com`? Decide by Wed EOD.
- **Q2 Corpus content** — ONE's real docs (privacy) or 5 public templates? Decide by Wed 10am.
- **Q3 Aitana admin branding** — Mark sees ONE branding when admin-debugging on the ONE deployment. Acceptable for demo period; flagged for follow-up when Aitana has its own commercial deployment.
- **Q4 PtX Tech Comparator data** — Hardcode from v5 prompt or wire to a real source? Stretch goal only in M3; default hardcode.
- **Q5 Telegram channel** — Out of scope this sprint per Non-Goals; confirm ONE accepts web-only for the demo.
- **Q6 ENTSO-E function set** — `entsoe_load` + `entsoe_generation_mix` deferred to v6.5 unless rehearsal surfaces a question they'd answer.
- **Q7 Diff alignment** — Highest in-sprint risk. Block_id matching + text-similarity fallback. Threshold tuning happens during M3 PM.
- **Q8 Single-doc clause surface** — Inline in chat (default) per design doc. Confirm Wed AM.

## Notes

- **No `?tenant=` URL parameter, no runtime branding endpoint, no hook/provider** — branding is build-time env vars. This is the deliberate architectural decision from 2026-06-08.
- **The existing `platform-frontend` Cloud Run service IS the ONE deployment.** No second service. No DNS work this week.
- **The template repo at `sunholo-data/ai-protocol-platform` keeps Sunholo branding** because env vars unset = Sunholo defaults render. `aitana-template-publish` skill refreshes that downstream without leaking ONE content.
- **Velocity assumption:** ~700 LOC/day fullstack at recent quality. Sprint totals ~2,800 LOC over 4 days — buffer-thin but achievable.
- **Rehearsal-first culture:** Thu EOD smoke is the demo-shipping gate. If doc-compare isn't smoke-passing Thu PM, M4 becomes M3 overflow Fri AM and the demo de-scopes (clause extraction + ENTSO-E carry).
