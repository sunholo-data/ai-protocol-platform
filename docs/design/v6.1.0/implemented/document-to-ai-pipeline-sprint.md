# Sprint: Document-to-AI Pipeline (1.9)

**Sprint ID:** DOC-AI-PIPELINE
**Design doc:** [document-to-ai-pipeline.md](document-to-ai-pipeline.md)
**Scope:** Backend only
**Estimated:** 2 days
**Status:** Complete (M1–M4 done — see commits feat(M2)–feat(M4) + chore(DOC-AI-PIPELINE))

---

## Sprint Goal

After a document is uploaded and parsed, clicking it in the file browser opens a skill chat where the AI immediately has access to the document's full structured content — and can cite specific pages and sections. Zero frontend changes.

---

## Velocity Reference

14-day velocity: 187 commits, heavy backend work on error-surfacing, session history, and rich-media. Recent backend milestones completed in 0.25–0.75d each. Estimates calibrated accordingly.

---

## Pre-flight: Existing Test Failures

7 tests failing before we start. **Must fix first** — sprint cannot pass CI with pre-existing red.

| Test file | Root cause |
|-----------|-----------|
| `test_document_context.py` (4 tests) | Fixture uses `"status": "parsed"` but `build_document_context` reads `"parseStatus"` |
| `test_upload.py` (3 tests) | `_run_parse` now returns 5-tuple; test mocks still expect 4-tuple |

Fix in M0 before any new code.

---

## Milestones

### M0 — Fix pre-existing test failures (~0.5d)
**Scope:** backend · **Depends on:** nothing

**Tasks:**
1. Read `test_document_context.py` fixtures — change `"status": "parsed"` → `"parseStatus": "parsed"` (or fix the field name mismatch in `build_document_context`)
2. Read `test_upload.py` — update mock return values to 5-tuple `(status, blocks, summary, elapsed_ms, error)` to match current `_run_parse` signature

**Acceptance criteria:**
- [ ] `pytest tests/tool_tests/test_document_context.py tests/tool_tests/test_upload.py` — all pass
- [ ] `make test-fast` — 622+ passing, 0 failing

---

### M1 — Artifact service singleton + `app_name` constant (~0.25d)
**Scope:** backend · **Depends on:** M0

**Tasks:**
1. `adk/session.py`: convert `get_artifact_service()` to a process-level singleton (mirror the pattern already used for `_session_service_singleton`)
2. Add `_reset_artifact_service_for_tests()` helper for test isolation
3. Add `ADK_ARTIFACT_BUCKET=your-project-id-artifacts` to `backend/.env` (with comment) and `backend/.env.example`
4. `adk/agui.py` already has `APP_NAME = "aitana_platform"` — no new constant needed. Add a startup `log.info` in `fast_api_app.py` confirming the artifact service backend (GCS bucket name or "in-memory")

**Key detail — `app_name`:**
`adk/agui.py` defines `APP_NAME = "aitana_platform"`. The upload pipeline's `before_agent_callback` uses this same value. Already consistent — no change needed.

**Acceptance criteria:**
- [ ] Two calls to `get_artifact_service()` return the same object instance
- [ ] With `ADK_ARTIFACT_BUCKET` unset → `InMemoryArtifactService`
- [ ] With `ADK_ARTIFACT_BUCKET` set → `GcsArtifactService(bucket_name=...)`
- [ ] `pytest tests/unit/test_session_services.py` passes (or new test file)

---

### M2 — `load_artifacts_tool` always-on in every agent (~0.25d)
**Scope:** backend · **Depends on:** M1

**Tasks:**
1. `adk/agent.py` line 279: add `load_artifacts_tool` to the always-on tools list alongside `retrieve_artifact`
   ```python
   from google.adk.tools.load_artifacts_tool import load_artifacts_tool

   tools = [
       load_artifacts_tool,
       retrieve_artifact,
       *resolve_tools(md.tools, md.tool_configs),
   ]
   ```
2. Write unit test asserting every agent produced by `create_agent()` has a tool named `"load_artifacts"`

**Acceptance criteria:**
- [ ] `create_agent(sample_skill, sample_user)` → tool list contains `"load_artifacts"`
- [ ] `make test-fast` still clean

---

### M3 — `before_agent_callback` document loader (~0.75d)
**Scope:** backend · **Depends on:** M1, M2

This is the core milestone. Three sub-tasks:

**3a. Verify `CallbackContext.save_artifact` API**
Before writing the callback, confirm whether `save_artifact` on `CallbackContext` is sync or async in ADK 1.29.0:
```bash
cd backend && uv run python -c "
import inspect
from google.adk.agents.callback_context import CallbackContext
print(inspect.iscoroutinefunction(CallbackContext.save_artifact))
"
```
If async → `make_document_loader` returns `async def _loader`; compose accordingly.

**3b. `make_document_loader` in `adk/callbacks.py`**
```python
def make_document_loader(document_id: str | None) -> Any:
    """Return a before_agent_callback that loads document blocks into session artifacts.

    - Fires only on first turn (guarded by app:docs_loaded state flag)
    - Fetches parsed blocks from Firestore via build_document_context(mode="blocks")
    - Saves as session-scoped artifact: doc:{document_id}.json (application/json)
    - On failure: sets app:doc_load_error in state — non-fatal
    """
```

Key points:
- Filename: `doc:{document_id}.json` (no `user:` prefix — session-scoped)
- MIME type: `application/json`
- Content: `json.dumps(blocks).encode("utf-8")` where `blocks` is the list from `build_document_context`
- Guard: skip if `state.get("app:docs_loaded")` is True
- Error: set `state["app:doc_load_error"]` and `state["app:docs_loaded"] = True`, log warning, do not raise

**3c. Wire into `create_agent` in `adk/agent.py`**
- `create_agent()` receives `document_id` from `access_context` or a new explicit param
- Compose `_document_loader` into `_composed_before_agent`
- `skill_processor.process_skill_request()`: pass `document_id` in `RunAgentInput.state` so it's available to the session

**3d. Pass `document_id` from stream request**
- `skill_processor.py`: add `document_id: str | None = None` param
- `RunAgentInput(state={"document_id": document_id} if document_id else {})`
- The SSE endpoint (`protocols/agui.py` or similar) needs to read `document_id` from the HTTP request body and pass it through

**Acceptance criteria:**
- [ ] `make_document_loader(None)` → returns a no-op callable
- [ ] `make_document_loader("doc123")` with parsed doc in Firestore → `save_artifact("doc:doc123.json", ...)` called on first turn only
- [ ] Second turn → `save_artifact` not called again (state flag)
- [ ] Firestore fetch failure → `state["app:doc_load_error"]` set, callback does not raise
- [ ] `state["app:docs_loaded"]` is True after first turn regardless of outcome
- [ ] `pytest tests/tool_tests/test_document_loader.py` — all cases pass

---

### M4 — GCS bucket Terraform + smoke test (~0.25d)
**Scope:** backend/infra · **Depends on:** M3

**Tasks:**
1. Confirm whether `your-project-id-artifacts` GCS bucket exists:
   ```bash
   gsutil ls gs://your-project-id-artifacts/ 2>&1
   ```
   If not: create it (or note as a manual step — bucket creation is a one-liner)
2. Add `ADK_ARTIFACT_BUCKET` to Cloud Run environment variables in Terraform for dev/test/prod
3. Add a `make smoke-artifacts` target that:
   - Writes a test artifact via the artifact service
   - Reads it back
   - Confirms round-trip works
4. Add `ADK_ARTIFACT_BUCKET` to the CI environment (`.github/workflows/ci.yml`) as empty string so unit tests use InMemory

**Acceptance criteria:**
- [ ] `gsutil ls gs://your-project-id-artifacts/` — bucket exists
- [ ] `make smoke-artifacts` passes against dev GCS bucket
- [ ] Terraform plan for dev shows `ADK_ARTIFACT_BUCKET` env var on Cloud Run service

---

## Day-by-Day Plan

| Day | Work |
|-----|------|
| Day 1 AM | M0: fix 7 pre-existing test failures |
| Day 1 PM | M1: artifact service singleton + bucket config; M2: `load_artifacts_tool` always-on |
| Day 2 AM | M3: implement `make_document_loader` + wire into agent + session state passing |
| Day 2 PM | M3: tests; M4: bucket + Terraform; final `make test-fast` + manual smoke test |

---

## LOC Estimates

| Milestone | Implementation | Tests | Total |
|-----------|---------------|-------|-------|
| M0 | 20 | 0 (fix existing) | 20 |
| M1 | 25 | 30 | 55 |
| M2 | 5 | 20 | 25 |
| M3 | 80 | 100 | 180 |
| M4 | 30 | 15 | 45 |
| **Total** | **160** | **165** | **~325** |

---

## Quality Gates

After each milestone: `cd backend && make test-fast`

After all milestones:
```bash
cd backend && make test          # full suite including integration
cd backend && make lint          # ruff
make smoke-artifacts             # GCS round-trip
```

Manual smoke test (requires `make dev` running):
1. Upload a `.docx` → green dot appears
2. Open skill chat with `document_id` → send "Summarize this document"
3. AI responds with content from the document (not "please specify")

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `CallbackContext.save_artifact` is async → composed callback needs to be async too | Medium | Check in M3a before writing; async callbacks are supported in ADK |
| `app_name` mismatch between agui.py runner and artifact store | Low | Already verified: `APP_NAME = "aitana_platform"` in `adk/agui.py` |
| `your-project-id-artifacts` bucket doesn't exist | Medium | M4 checks and creates if needed; unblocks M3 testing via InMemory |
| `document_id` not plumbed through the HTTP request body | Low | Already present in session state model; need to confirm the SSE endpoint reads it |
