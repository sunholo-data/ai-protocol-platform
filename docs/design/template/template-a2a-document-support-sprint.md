# Sprint A2A-DOCS — A2A document support (G46)

**Status**: Planned
**Sprint ID**: `A2A-DOCS`
**G-number**: G46
**Duration**: 1-2 days
**Scope**: Backend + Cloud Build + CLI + Tests + Docs
**Design doc**: [template-a2a-document-support.md](template-a2a-document-support.md)
**Source brief**: `<local-path>` §6 (lines 1178-1356) + Frictions 29-30 (lines 1358-1443)
**Created**: 2026-06-08

## Goal

Bring the G45 invocation surface from "text-only" to "files inbound + org-bucket query", matching gde-ap-agent's production-validated implementation. End-state: GE peer can upload a PDF/DOCX and the agent answers from its content; peer can query an existing org bucket via two tools.

Success metric: live POST to `/a2a` with a FilePart returns HTTP 200 with `doc:{id}.json` persisted + agent grounds response on document. `aiplatform a2a probe-org-bucket` lists objects on the deployed Sunholo deploy.

## Reference implementation

gde-ap-agent commits to port verbatim:
- `aaf0315` — M1 file extraction (`backend/protocols/file_extraction.py`)
- `10f7a93` — M2 org-scoped bucket (`backend/tools/org_documents.py`)
- `7687784` — M3 `force_new_version=True` fix on the executor

## Milestones

### M1 — File-input interceptor + force_new_version fix (TDD-first, ~3h)

**Scope:** backend (3 files, 2 new + 1 edit)
**LOC:** ~200 impl + ~150 tests

Tasks:
1. Create `backend/tests/api_tests/test_file_extraction.py` with 5 tests FIRST. Critical: regression guard asserting `A2aAgentExecutor` is constructed with `force_new_version=True` (would catch Friction 29).
2. Create `backend/protocols/file_extraction.py` (~200 LOC). Port verbatim from gde-ap-agent's `aaf0315`. Adapt imports as needed (artifact_service singleton from our `adk.session`).
3. Edit `backend/protocols/a2a_invocation.py`: add `force_new_version=True` + `config=A2aAgentExecutorConfig(execute_interceptors=[FileExtractionInterceptor()])` to executor construction. Env-gate the interceptor list — empty when `ENABLE_A2A_FILE_INPUT` is unset.
4. Run `cd backend && make lint && make test-fast` — all 5 new tests pass + G45's 6 invocation tests still green + G43's 11 discovery tests still green.

**Acceptance:**
- 5 new tests pass; 17 existing a2a tests still green (6 invocation + 11 discovery)
- Test asserts `force_new_version=True` on executor (Friction 29 regression guard)
- Test asserts FilePart stripped from message.parts after interceptor (double-injection guard)
- Test asserts no-op when `ENABLE_A2A_FILE_INPUT` unset
- `cd backend && make lint && make test-fast` both green

### M2 — Card defaultInputModes extension (~30min)

**Scope:** backend (1 file)
**LOC:** ~15 impl + ~30 tests

Tasks:
1. Edit `backend/protocols/a2a.py:_build_card`: change `defaultInputModes: ["text"]` to the 9-MIME-type list from §6a. Add `A2A_AGENT_INPUT_MIME_TYPES` env var (comma-separated override). Default matches the brief.
2. Add 2 tests: card emits the extended default; env override replaces the default.

**Acceptance:**
- Card emits `defaultInputModes` with at least PDF + DOCX + plain text
- `A2A_AGENT_INPUT_MIME_TYPES="text,application/pdf"` env results in card emitting exactly those two
- Existing a2a tests still pass

### M3 — Org bucket tools + root_agent wiring (~3h)

**Scope:** backend (2 files, 1 new + 1 edit)
**LOC:** ~200 impl + ~100 tests

Tasks:
1. Create `backend/tools/org_documents.py` (~200 LOC). Port verbatim from gde-ap-agent's `10f7a93`. Two `FunctionTool`s: `list_org_documents(prefix="")` + `read_org_document(name)`. Both env-gated by `A2A_AGENT_DOCUMENTS_BUCKET`; both return graceful "no bucket bound" responses when unset.
2. Edit `backend/app.py`: conditionally append `[list_org_documents, read_org_document]` to `root_agent.tools` when env var is set. Comment on the boundary (orchestrator instruction extension).
3. Add 4 tests: tools list+read against a real GCS bucket via fake/test fixture; both tools return graceful no-bucket response when unset; SA-missing-objectViewer logs warning + returns graceful.

**Acceptance:**
- `list_org_documents()` returns `[]` when env unset
- `read_org_document("x.pdf")` returns `{"ok": False, ...}` when env unset
- With test bucket set, tools list + load + appended to `state["document_ids"]`
- Tools NEVER 500 the agent turn (gracefully degrade on auth or quota errors)

### M4 — CLI + Cloud Build + Sunholo deploy (~1.5h)

**Scope:** CLI + Cloud Build + ops
**LOC:** ~60 CLI + ~10 cloudbuild

Tasks:
1. Create `cli/aitana/a2a.py` (or extend existing CLI group). Two Click commands:
   - `aiplatform a2a probe-org-bucket` — checks env on deployed service, runs SA-authed list call, prints object count + first 5 names
   - `aiplatform a2a send-file <local-path> --skill <skill-id>` — POSTs message/send with FilePart to local `/a2a`, prints doc_ids + agent reply
2. Edit `cloudbuild.yaml` (root, the frontend deploy): add `ENABLE_A2A_FILE_INPUT=true` + `A2A_AGENT_DOCUMENTS_BUCKET=gs://aitana-public-bucket/demo/` (or pick a sensible Sunholo bucket). Keep `\` line continuation pattern from G45-PARSE-FIX.
3. Grant frontend SA `roles/storage.objectViewer` on the chosen bucket (manual via `gcloud projects add-iam-policy-binding` OR Terraform if standard pattern).
4. Push, wait deploy, verify env vars landed (lesson from G45-PARSE-FIX), run `aiplatform a2a probe-org-bucket` against deployed.

**Acceptance:**
- 2 new CLI commands work locally (`aiplatform a2a --help` shows them)
- Deploy succeeds with the 2 new env vars actually on the running revision
- `aiplatform a2a probe-org-bucket` against deployed succeeds + lists objects
- Live test: POST `.docx` via simulate-a2a-peer.py (extended in M5) returns 200 with doc_ids

### M5 — Docs + scripts + sprint close (~1h)

**Scope:** Docs + scripts
**LOC:** ~70 across 3 files

Tasks:
1. Extend `scripts/simulate-a2a-peer.py` with a "Step 4b — send a FilePart" demonstrating Scenario A end-to-end against deployed.
2. Extend `scripts/verify-a2a.sh` to assert `defaultInputModes` includes file MIME types when `ENABLE_A2A_FILE_INPUT` is set.
3. Update `docs/integrations/gemini-enterprise.md` Troubleshooting: add "agent doesn't see uploaded files" (Friction 29 force_new_version), "custom metadata field rejected" (Friction 30), "org bucket lists empty" (IAM grant missing).
4. Move design doc to Implemented status. Update SEQUENCE.md.

**Acceptance:**
- `simulate-a2a-peer.py` shows green file-send step against deployed
- `verify-a2a.sh` against deployed: all checks pass + new defaultInputModes check green
- Troubleshooting section has 3 new failure-mode entries
- SEQUENCE.md G46 row flipped from Planned → Shipped

## Day-by-day plan

**Day 1 (4-5h):**
- Morning: M1 (TDD-first; 5 tests then port file_extraction.py + wire force_new_version)
- Afternoon: M2 (card defaultInputModes) + M3 (org bucket tools + tests)
- Validation gate EOD: `make lint && make test-fast` green; local `make dev` smoke — POST a `.docx` to `localhost:1956/a2a/` returns 200 with `doc:{id}.json` artifact

**Day 2 (3-4h):**
- Morning: M4 (CLI + cloudbuild + Sunholo deploy + Cloud Build wait + env-vars-actually-landed check)
- Afternoon: M5 (docs + simulate-a2a-peer extension + verify-a2a extension)
- Final gate: live GE test — upload a file in GE workspace UI, agent responds with grounded reference

## Success criteria

- [ ] All 5 new file_extraction tests + 4 new org_documents tests pass; 17 existing a2a tests still green
- [ ] `force_new_version=True` regression guard test in place
- [ ] Double-injection regression guard test in place
- [ ] Deploy lands with `ENABLE_A2A_FILE_INPUT=true` + `A2A_AGENT_DOCUMENTS_BUCKET=...` on the running revision (verify with `gcloud run services describe` per the G45-PARSE-FIX lesson)
- [ ] `aiplatform a2a probe-org-bucket` against deployed: lists objects
- [ ] `simulate-a2a-peer.py` against deployed: Step 4b (file-send) green
- [ ] Live GE upload → agent reads file → grounded response
- [ ] Design doc Status: Implemented; SEQUENCE.md updated

## Scope cuts (if Day 2 proves tight)

In priority order:

1. **Defer:** the live GE upload test (M4 final acceptance). simulate-a2a-peer.py is the equivalent local proof — GE upload can be confirmed manually post-sprint.
2. **Defer:** SA grant via Terraform — do it manually with `gcloud projects add-iam-policy-binding` instead. Document as follow-up.
3. **Defer:** the second CLI command (`aiplatform a2a send-file`). The probe one is more useful for ops; send-file is dev-loop only.
4. **Defer:** verify-a2a.sh extension (M5 task 2). Already green for discovery + invocation; the defaultInputModes check is nice-to-have.

## Friction 29 guard (NEVER SKIP)

The single highest-value test in this sprint:

```python
def test_a2a_executor_constructed_with_force_new_version_true():
    """Regression guard for Friction 29.

    Without force_new_version=True, ADK's A2aAgentExecutor picks the LEGACY
    path that bypasses interceptors entirely. Code looks correct, tests pass,
    production silently drops every file. Real failure 2026-06-08T04:45 UTC
    on gde-ap-agent — cost ~90 min to diagnose.

    If a future refactor removes the flag, this test fails BEFORE the
    interceptor goes inert in production.
    """
    from protocols.a2a_invocation import build_a2a_app
    # ... assert executor.force_new_version is True
```

If this test is missing, ANY refactor of `build_a2a_app()` can silently
re-introduce Friction 29 in production. Belt + suspenders.

## Risks summary

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ADK A2A executor API change (`@a2a_experimental`) | Medium | Sprint blocked | Pin `google-adk` version to match gde-ap-agent's working deploy |
| Mount order regressions (M1 edits to a2a_invocation.py) | Low | /a2a 404 in dev | Existing M2 G45 mount-order test catches |
| Cloud Build line-continuation bug returns (G45-PARSE-FIX) | Low | Env vars dropped | All new --set-env-vars lines in M4 must have `\` continuation with NO `#` comments between |
| GCS SA grant forgotten | Medium | Org tools return empty | M3 graceful-degradation tests + clear "no objects + warning logged" UX makes diagnosis obvious |
| GE doesn't actually deliver FilePart even with extended MIMEs | Medium | M4 live test fails | simulate-a2a-peer file-send (Day 2 morning) is the local equivalent; if GE-specific failure, defer to follow-up |
| agents-cli dedupe quota collision on Sunholo re-register | High | Console clutter | Use existing Friction 28 workaround (manual DELETE then register) |
