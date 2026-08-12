# v6.5.0 Build Sequence

**Gate:** v6.4.0 substantially complete (4.5 SKILL-ONBOARDING ✅ shipped 2026-06-10; 4.5.1 document-import-by-reference scheduled for Thu 2026-06-11 pre-demo).

**Status as of 2026-06-11:** Two docs planned — `parsed-blocks-pipeline.md` (5.1, sprint key PARSED-BLOCKS) and `private-content-leak-prevention.md` (5.2, sprint key PRIVCONTENT-GUARD; exploratory, ship-later). Other v6.5.0 candidates carried over from v6.3.0/v6.4.0 deferrals (shared/team RAG corpora, real-time collaborative editing, Gemini Live voice, skill marketplace, teacher/operator-dashboard pattern, template publication of v6.4.0 visual layer) are not yet planned.

**Theme:** Shared parse pipeline + structural + security defense-in-depth. 5.1 takes the v6.4.0 4.5.1 sentinel-user L4 pattern to its logical conclusion (shared `parsed_blocks` Firestore collection fed by the multivac chunker, eliminates duplicate parses + collapses per-user storage). 5.2 hardens the architectural boundary around private content: in response to a 2026-06-11 public-thumbnail near-miss, layer IAM scoping + pre-commit + CI lint on top of the CLAUDE.md rule so the next "but it's just a thumbnail" mistake is refused at the GCS write layer, not caught at the customer-noticing-it layer.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 5.1 | [parsed-blocks-pipeline.md](parsed-blocks-pipeline.md) | **P1** | ~3d (cross-repo: multivac-system-services + multivac-aitana infra + platform) | v6.4.0 4.5.1 [document-import-by-reference.md](../v6.4.0/document-import-by-reference.md) (sentinel-user L4 must ship first — L5 extends its cascade), multivac chunker AILANG Parse migration (`e8d3ece`, 2026-06-03 ✅) | **Shared parse pipeline.** Adds `aitana_blocks` vectorstore branch to multivac chunker `dispatch_parsed_doc` publishing the raw Block ADT (not the lossy markdown of `chunker-to-aisearch` nor the per-chunk split of `chunk-to-pubsub-embed`) to new topic `chunker-to-aitana-blocks`. Aitana subscriber writes to new shared Firestore collection `parsed_blocks` keyed by `sha256(gs_url)`. `import-by-reference` gains L5 shared lookup tier; per-user `parsed_documents` records grow optional `blocksRef` field pointing at shared rows (legacy inlined-`blocks` records remain readable via `get_blocks()` helper). Backfill via new Cloud Run Job (`aiplatform docs batch-parse`) that publishes synthetic OBJECT_FINALIZE events to `app-to-pubsub-chunk`, riding the existing chunker flow. Net axiom score +8 — strongest hits on INSTANT FEEL (+1, first-click for chunker-ingested docs collapses to ≤500ms), RIGHT MODEL RIGHT MOMENT (+1, eliminates duplicate AILANG Parse charges), OBSERVABLE BY DEFAULT (+1, BigQuery `parsed_blocks_archive` sink mirroring qna pipeline). New CLI: `aiplatform docs batch-parse / blocks-status / blocks-show`. Cross-repo PRs in multivac-system-services + multivac-aitana + platform. |
| 5.2 | [private-content-leak-prevention.md](private-content-leak-prevention.md) | **P1** | ~1.6d (incrementally landable; IAM scoping alone is ~0.25d) | Existing CLAUDE.md "Security Hard Rules" section (✅, 2026-06-11 commit 88f6a86), existing `gs://aitana-public-bucket/`, existing tagged-access buckets | **Defense-in-depth against private-content leaks** to public surfaces (e.g. `gs://aitana-public-bucket/`). Response to a 2026-06-11 near-miss where page-1 PPA thumbnails were published to the public bucket. Three independent layers: (1) **IAM scoping** — dedicated `roles/aitanaPublicAssetsWriter` custom role; dev SA loses project-level `storage.objectAdmin`; legitimate ops join `public-assets-writer@` group per session. (2) **Pre-commit hook** scans staged diffs for `storage.googleapis.com/aitana-public-bucket/<prefix>/...` URLs and refuses commits unless the prefix is in `infrastructure/public-paths.yaml`. (3) **CI lint** runs the same check on every PR. Plus an **approved-paths manifest** under CODEOWNERS. Net axiom score +6 — strongest hits on EARNED TRUST (+2) and SECURE BY CONSTRUCTION (+2). No CLI surface (operator workflows use existing `gcloud`). Recommended landing order: M1 manifest → M2 pre-commit → M3 CI → M4 IAM role → M5 dev-SA role removal (gated on team awareness) → M6 verification. |
| 5.3 | [mcp-app-workbench-tab-source.md](mcp-app-workbench-tab-source.md) | P2 | ~1.5d (gated on a consumer) | v6.4.0 SHELL-MODES ✅, v6.1.0 mcp-app-integrations ✅ | **Resolves the v6.4.0 SHELL-MODES follow-up** for the placeholdered `mcp_app:<server>` workbench-tab content source. MCP apps are tool-call-result-driven, so a declared tab has no result to render. Options analysis (synthesise a direct-client tool call on mount / passive-init render contract / retire the source) with a recommendation: **Option A — direct MCP client call on tab mount, reusing the passive-render plumbing, gated on a real `workbench-primary` consumer.** Resting state until then: explicit placeholder + schema marked "reserved". Net axiom +8 — PROTOCOL OVER CUSTOM (+2), GRACEFUL DEGRADATION (+2). |
| 5.4 | [authenticated-landing.md](authenticated-landing.md) | **P1** | ~2.5d | v6.3.0 client-tenant-management ✅, v6.4.0 4.2 enabled_skills filter ✅, domain-derived group tags ✅ | **Customer feedback 2026-06-19.** Signed-in users land directly on their last chat (or a fresh primary-skill chat); homepage becomes the logged-out-only front door; in-app skills narrowed per client. New `GET /api/sessions/recent` resolver + `ClientConfig.default_skill` + auth-aware `HomeGate` redirect (fallback chain: last session → primary fresh chat → marketplace). Routing is platform-wide; skill set is per-client config (`aiplatform client set --enabled-skills … --default-skill …`). ONE config: one-ppa-expert + one-doc-compare + web-researcher, primary one-ppa-expert. Net axiom +8 — INSTANT FEEL (+2), GRACEFUL DEGRADATION (+2). |
| 5.5 | [bucket-browser-and-doc-compare-files.md](bucket-browser-and-doc-compare-files.md) | **P1** | ~2d | v6.4.0 SHELL-MODES ✅, 4.5 SKILL-ONBOARDING ✅ | **Customer feedback 2026-06-19 (file browsing).** (A) Restore bucket + example-doc access in doc-compare mode — DocCompareShell dropped the sidebar, so one-doc-compare's `welcome.bucket_browser`/`example_documents` had nowhere to render; surface them in the pre-comparison empty state, wired to import-by-reference. (B) `GCSFileBrowser` "Load more" — backend `/api/buckets/{name}/list` already paginates (folder-scoped, fixes the v5 large-bucket lag) but the frontend ignored `nextPageToken`, silently truncating folders at 100. Frontend-only. Net axiom +7 — GRACEFUL DEGRADATION (+2). |
| 5.6 | [a2ui-over-mcp.md](a2ui-over-mcp.md) | P2 | ~1.5d (gated on a consumer) | v6.1.0 a2ui-tool-delivery ✅, v6.1.0 mcp-app-integrations ✅, v6.1.0 mcp-app-update-model-context ✅, v6.2.0 multi-surface-rendering ✅ | **Design-ahead from Google's [A2UI + MCP Apps blog](https://developers.googleblog.com/a2ui-and-mcp-apps/) (the blog's Pattern 1).** Today only the in-process ADK agent emits native A2UI (`send_a2ui_json_to_client`); a *remote* MCP tool can only contribute UI as a sandboxed `text/html;profile=mcp-app` iframe. This lets a remote MCP tool return `application/a2ui+json` (static `resources/read` via `a2ui://` URI, or dynamic `tools/call` result) so structured UI renders **natively in our catalog** instead of a design-system-clashing iframe — matching the per-deploy-branding stance. Pure additive content-type branch on the existing [MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx) `readResource`/`parseToolResult` hook points + host-side catalog validation (the security linchpin — third-party A2UI is untrusted). Doc also records where v6 already aligns with the blog (Patterns 2 + the capability-based model) and the conscious "local-toolset-over-AG-UI" divergence from the blog's two delivery channels. **Gated on a real consumer** (a remote MCP tool wanting native structured UI); resting state = this doc + flag-off. Net axiom +7 — PROTOCOL OVER CUSTOM (+2). |
| 5.7 | [mcp-external-oauth.md](mcp-external-oauth.md) | P2 | ~1d resource-server + Option-A IDP spike · ~3–4d Option-A′ platform-as-AS (Phase 2) | `mcp_proxy` (the Firebase-gated path), `backend/auth` dispatcher (the sprint-2.11 group-id-auth pattern for a new `auth_mode`), external-host serving | **Production auth for external MCP hosts.** ChatGPT/Copilot connectors can't present a Firebase token, so the deployed `/mcp` (Firebase + allowlist via `mcp_proxy`) is unreachable externally; anonymous exposure (`mcp-artefact-test` + cloudflared) is dev-only. Adopts the **MCP Authorization spec** (OAuth 2.1, RFC 9728/8414/7636/8707): a **resource-server layer** (`/.well-known/oauth-protected-resource` + bearer JWT validation + `401`/`WWW-Authenticate` + `securitySchemes`) on a NEW external ingress that **coexists** with the Firebase proxy; validated `sub`→`User` reuses the allowlist. Core decision **A vs A′ (NOT B):** **A** = delegate to a third-party IDP (Auth0/Stytch — fast ~½–1d, but login crosses the GCP edge); **A′** = platform becomes its own AS via `authlib` backed by Firebase (identity stays inside the edge, existing accounts, ~3–4d); **B** (hand-rolled AS) rejected. Recommends **phased A-PoC → A′-prod**. New CLI `aitana mcp probe`. Net axiom **+5** — PROTOCOL OVER CUSTOM, SECURE BY CONSTRUCTION, API FIRST, OBSERVABLE, GRACEFUL DEGRADATION. Gated on a committed external-user surface. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 5.1 | [parsed-blocks-pipeline.md](parsed-blocks-pipeline.md) | Planned 2026-06-10 |
| 5.2 | [private-content-leak-prevention.md](private-content-leak-prevention.md) | Planned 2026-06-11 (exploratory; ship after v6.5.0 5.1 lands and demo dust settles) |
| 5.3 | [mcp-app-workbench-tab-source.md](mcp-app-workbench-tab-source.md) | Planned 2026-06-13 (design-ahead; implementation gated on a `workbench-primary` consumer) |
| 5.4 | [authenticated-landing.md](authenticated-landing.md) | ✅ Implemented 2026-06-19 (sprint AUTH-LANDING; deployed to dev) |
| 5.5 | [bucket-browser-and-doc-compare-files.md](bucket-browser-and-doc-compare-files.md) | ✅ Implemented 2026-06-19 (sprint BUCKET-FILES; deployed to dev) |
| 5.6 | [a2ui-over-mcp.md](a2ui-over-mcp.md) | Proposed 2026-06-23 (design-ahead from Google A2UI+MCP-Apps blog; implementation gated on a remote-MCP-tool consumer) |
| 5.7 | [mcp-external-oauth.md](mcp-external-oauth.md) | Proposed 2026-07-08 (from the MCP-Apps workshop; implementation gated on a committed external-host production surface) |

## What ships in v6.5.0

**From 5.1 (parsed-blocks-pipeline) — shared parse storage layer:**
- **`aitana_blocks` vectorstore branch in multivac chunker** — new `dispatch.py` branch publishes raw Block ADT JSON to new topic `chunker-to-aitana-blocks`. Coexists with existing DE + embed branches; dead code until a VAC config opts in.
- **`parsed_blocks` Firestore collection** — shared content layer keyed by `sha256(gs_url)`. Stores the full Block ADT + `docMetadata` (title, author, page_count, structural summary counts) + provenance fields (`vectorName`, `ingestedBy`, `ingestedAt`).
- **`parsed_documents.blocksRef`** — optional reference field on per-user records. When set, the per-user record is a thin metadata shell (~1 KB) pointing at shared blocks instead of inlining them. `get_blocks(doc)` helper handles both legacy + new shapes transparently.
- **`import-by-reference` L5 tier** — new shared-content lookup between L2 (per-user) and L4 (sentinel-user). Three Firestore reads + one write to mount a chunker-ingested doc — no parse, no AI credits.
- **`aitana-parsed-blocks-writer` push endpoint** — `/api/internal/parsed-blocks/ingest` consumes the topic via PubSub push subscription; OIDC + allowlist-gated.
- **Batch-parse Cloud Run Job** — `aiplatform docs batch-parse --bucket X --prefix Y --vector-name V` walks a bucket and publishes synthetic OBJECT_FINALIZE events to `app-to-pubsub-chunk`, letting the existing chunker do the heavy lifting.
- **CLI:** `aiplatform docs batch-parse`, `aiplatform docs blocks-status`, `aiplatform docs blocks-show`.
- **Observability:** OTEL spans on the writer service; BigQuery `parsed_blocks_archive` table mirrors the `qna-to-pubsub-bq-archive` audit pattern.

**From 5.2 (private-content-leak-prevention) — defense-in-depth against publishing customer-derivative content:**
- **`infrastructure/public-paths.yaml`** — explicit allowlist of approved prefixes under `gs://aitana-public-bucket/` (`branding/`, `template-assets/`, `demo/`, etc.) with one-line rationale per entry. Changes gated by CODEOWNERS reviewer.
- **Pre-commit hook + CI lint** (`scripts/check-public-paths.sh`) — refuses any commit/PR that introduces a `storage.googleapis.com/aitana-public-bucket/<unapproved-prefix>/...` URL to any tracked file.
- **`roles/aitanaPublicAssetsWriter` custom IAM role** + `public-assets-writer@yourcompany.com` Google Group — bucket-scoped `storage.objects.create/delete/update`. Dev SA loses project-level `storage.objectAdmin`. Operators join the group explicitly per session for legitimate ops.
- **Edge-cache stale-serve runbook** — documents the gotcha that GCS public-object delete alone doesn't invalidate Google's edge cache; overwrite-with-blank + `Cache-Control: no-cache` is the fast remediation path.
- **CLAUDE.md update** — "Security Hard Rules" section links this design doc and the pre-commit install instructions.

## Dependency Graph

```
v6.4.0 4.5 SKILL-ONBOARDING (✅, 2026-06-10)
   │
   └──► 4.5.1 document-import-by-reference (Thu 2026-06-11)
            │
            └──► 5.1 parsed-blocks-pipeline (v6.5.0)
                     │
                     ├── multivac chunker (AILANG Parse, e8d3ece ✅)
                     │      │
                     │      └── new dispatch branch: aitana_blocks vectorstore
                     │             │
                     │             └── publishes Block ADT to chunker-to-aitana-blocks
                     │
                     ├── multivac-aitana terraform — new topic + IAM grants
                     │
                     └── platform — parsed_blocks_writer subscriber +
                                    parsed_blocks Firestore collection +
                                    import-by-reference L5 tier +
                                    blocksRef on parsed_documents +
                                    batch-parse Cloud Run Job

CLAUDE.md "Security Hard Rules" (✅, 88f6a86 — 2026-06-11)
   │
   └──► 5.2 private-content-leak-prevention (v6.5.0; can land any time after 5.1 demo dust settles)
            │
            ├── M1 infrastructure/public-paths.yaml manifest
            ├── M2 pre-commit hook + scripts/check-public-paths.sh
            ├── M3 GitHub Actions CI lint
            ├── M4 terraform: roles/aitanaPublicAssetsWriter + group + bucket binding
            ├── M5 remove project-level storage.objectAdmin from dev SA (gated on team comms)
            └── M6 verification — attempt the original 2026-06-11 upload; assert all 3 layers fire
```

## Next: v6.6.0

Not yet planned. v6.5.0 deferrals carry forward to v6.6.0 candidates: shared/team RAG corpora (per-skill), real-time collaborative editing, Gemini Live voice, skill marketplace, teacher/operator-dashboard pattern, template publication of v6.4.0 visual layer.
