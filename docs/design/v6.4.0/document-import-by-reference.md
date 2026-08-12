# Document Import by Reference

**Status**: Planned
**Priority**: P0 (High)
**Estimated**: 0.75 day (~65 LOC backend incl. L4 sentinel dedup + ~30 LOC frontend + ~40 LOC CLI incl. `--as-platform` flag + 4 pytest + 2 vitest)
**Scope**: Fullstack
**Dependencies**: 4.5 SKILL-ONBOARDING (M2 SkillExamplesPicker + M4 GCSFileBrowser) shipped 2026-06-10
**Sprint Key**: DOC-IMPORT-REF
**Created**: 2026-06-10
**Last Updated**: 2026-06-10

## Problem Statement

4.5 SKILL-ONBOARDING shipped picker + bucket-browser UIs that let users click an example PPA (or any bucket-listed file) instead of uploading their own. Both surfaces punted the "what happens on click" question to a synthetic chat-message hack:

```typescript
// frontend/src/app/chat/[...path]/page.tsx:802 (picker) and :661 (GCSFileBrowser)
const intent = `Load the example "${label}" from gs://${bucket}/${object} ...`;
void sendMessage(intent, { documentIds: includedDocIds, ... });
```

That dispatches to the agent's [`read_org_document`](../../../backend/tools/org_documents.py) tool, which downloads raw bytes as a base64 artifact — **no AILANG Parse, no Firestore caching, no workbench mount**. The LLM gets unparsed binary, "tries" to reason about it, and either bails out or hallucinates content. From Mark's local test 2026-06-10: "I tried to parse the example files and they failed."

**Current State:**
- Clicking an example PPA produces a chat turn the agent can't answer usefully (visual failure: no clauses extracted, no comparison rendered)
- Bucket-browser-picked files have the identical broken path (`onPick` is the same hack at [page.tsx:660-665](../../../frontend/src/app/chat/[...path]/page.tsx#L660-L665))
- Re-clicking the same file re-issues the same chat turn (no cache, no dedup); even if the agent's bucket tool worked, every click would download + base64-encode the file again
- The picker/browser are landing-page surfaces for the Fri 2026-06-12 ONE demo — broken UX here means the demo opens on a non-functional gesture

**Impact:**
- **Who**: every user of every skill with `welcome.example_documents` or `welcome.bucket_browser` declared. Today that's `one-ppa-expert` + `one-doc-compare` (both shipped 2026-06-10).
- **How significant**: blocks the Fri 2026-06-12 ONE demo's onboarding gesture. The demo opener IS the picker; if it can't parse the example, the rest of the script doesn't land.

## Goals

**Primary Goal:** When a user clicks an example or bucket-listed document, the parsed content is mounted in the workbench within ~3s and behaves identically to a hand-uploaded file from that point forward — no agent-side bucket reads, no synthetic chat turns, no re-parsing on re-click.

**Success Metrics:**
- Click → parsed + mounted in ≤ 3s P50 / ≤ 5s P95 for a typical 30-page PPA PDF (first parse)
- Re-click on the same `(user, gs://...)` returns the existing parsed record in ≤ 200ms (dedup path)
- 100% of example PPAs in `one-ppa-expert.welcome.example_documents` parse successfully (5/5)
- Zero new "parse failed" backend log entries during the Fri demo's onboarding gesture
- Zero new parser implementations — the existing `_run_parse` / `_store_document` pipeline is the only code path

**Non-Goals:**
- Cross-user block dedup (sharing parsed blocks across `userId` when `sourceUrl` matches). Possible Firestore-cost win, no UX win — deferred to v2.
- A new bucket-access policy. The picker + browser already enforce access via `welcome.bucket_browser` whitelisting; this doc reuses that policy unchanged.
- Streaming the parse progress to the UI. Loading-state spinner is sufficient for ~3s budgets.
- Rewriting the agent's `read_org_document` tool. It stays for whatever legitimate "fetch raw bytes" use cases exist (sub-agent diagnostics, format inspection); the user-facing pickers stop using it.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | First-parse ~3s; **re-clicks ~200ms via existing dedup query + in-memory parse cache** ([ailang_parse.py:88-110](../../../backend/tools/documents/ailang_parse.py#L88-L110)). Eliminates the failed agent chat turn (~10s of LLM time on nothing). |
| 2 | EARNED TRUST | 0 | Parsing fidelity unchanged — same AILANG Parse path as uploads. Citations + block-level provenance flow identically. |
| 3 | SKILLS, NOT FEATURES | +1 | Picker + browser already declared in `SkillConfig.welcome` (4.5). This doc makes the declared resources actually work without users learning anything new. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | AILANG Parse is deterministic — zero LLM tokens for PDF/DOCX. Today's broken path forces the LLM to "reason about" base64 binary, which is the opposite. |
| 5 | GRACEFUL DEGRADATION | +1 | AILANG Parse already falls back to AI extraction for unsupported types ([ailang_parse.py:122](../../../backend/tools/documents/ailang_parse.py#L122)). Backend route returns 4xx with `parseError` field on permanent failure; frontend renders the existing upload-failure UI (no new error surface). |
| 6 | PROTOCOL OVER CUSTOM | +1 | Reuses the existing `/api/documents/*` REST convention + `ParsedDocumentResponse` shape. No new protocol, no new collection, no new event types. |
| 7 | API FIRST | +1 | New endpoint is channel-agnostic — Telegram/CLI/email can all "import this gs:// URL" via the same route. CLI affordance below explicitly tests this. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Inherits OpenTelemetry spans from the existing route (`fastapi-instrumentor` covers it); `parsedMs` + `parseError` + `parseStatus` already stored in Firestore. Add one `import_by_reference: dedup_hit / fresh_parse` log line for cache-hit visibility. |
| 9 | SECURE BY CONSTRUCTION | 0 | Uses existing access policy (`welcome.bucket_browser` whitelist enforces what buckets the picker can target; SA-credentialed backend proxy is the same one the 4.5 M4 list endpoint uses). No new trust relationships. Does NOT cross the GCP project edge. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Parse + dedup logic lives entirely in the backend; frontend just POSTs and mounts the response. Replaces a frontend-baked synthetic-chat-message hack with a real backend call. |
| | **Net Score** | **+7** | Threshold: ≥ +4. Strong alignment — proceed. |

**Conflict Justifications:**
- None. No axiom scored -1.

## Standards Compliance Check

Not applicable. This doc defines an internal HTTP route on the existing `/api/documents/*` namespace; it does not invent a new schema, protocol, or wire format. Reuses `ParsedDocumentResponse` ([upload.py:85-94](../../../backend/tools/documents/upload.py#L85-L94)) verbatim.

## Design

### Overview

Add a sibling route — `POST /api/documents/import-by-reference` — that takes `{bucket, object, skillId}` and runs the same parse + store internals as `/upload`, skipping the file→GCS upload step (the file already lives in GCS). Frontend rewires `onPickExample` and the GCSFileBrowser `onPick` callback to call this endpoint and mount the returned doc in the workbench like a normal upload. Dedup by `(userId, sourceUrl)` short-circuits re-clicks.

### Backend Changes

**New Endpoint:**

```python
# backend/tools/documents/import_by_reference.py — new module, ~50 LOC
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from .upload import (
    _run_parse, _store_document, _ParseResult,
    ParsedDocumentResponse, _COLLECTION,
)
from auth import User, get_current_user
from db.firestore import query_documents

router = APIRouter(prefix="/api/documents", tags=["documents"])


class ImportByReferenceRequest(BaseModel):
    bucket: str
    object: str
    skill_id: str = Field(default="", alias="skillId")
    model_config = {"populate_by_name": True}


@router.post("/import-by-reference")
async def import_by_reference(
    req: ImportByReferenceRequest,
    user: User = Depends(get_current_user),
) -> ParsedDocumentResponse:
    """Parse a GCS-resident document by reference. Same pipeline as /upload,
    minus the file → GCS step (the file already lives at gs://{bucket}/{object})."""
    gs_url = f"gs://{req.bucket}/{req.object}"

    # Dedup: same user already imported this gs://? Return the existing record.
    existing = query_documents(
        _COLLECTION,
        filters=[("userId", "==", user.uid), ("sourceUrl", "==", gs_url)],
        limit=1,
    )
    if existing:
        log.info("import_by_reference: dedup_hit user=%s gs=%s", user.uid, gs_url)
        return _to_response(existing[0])

    # Fresh parse — reuse the upload pipeline's internals verbatim.
    status, blocks, parsed_ms, parse_error = await _run_parse(gs_url)
    if status == "failed":
        raise HTTPException(status_code=422, detail=parse_error or "Parse failed")

    doc_id = str(uuid.uuid4())
    _store_document(
        doc_id,
        user_id=user.uid,
        skill_id=req.skill_id,
        gs_url=gs_url,
        storage_path=req.object,
        original_filename=PurePosixPath(req.object).name,
        source_format=PurePosixPath(req.object).suffix.lstrip("."),
        folder_id=None,  # imported docs don't belong to a user folder
        parse_result=_ParseResult(status, blocks, parsed_ms, parse_error),
        now=datetime.now(UTC),
    )
    return _to_response_for_new(doc_id, status, blocks, req.object)
```

Wire it in `backend/fast_api_app.py` next to the existing `upload` router include.

**Modified Files:**

- `backend/tools/documents/upload.py` — extract a `_to_response(doc_dict)` helper from the existing `upload_document` return path (the response is built today inline at [upload.py:307-313](../../../backend/tools/documents/upload.py#L307-L313); factor that into a function so both routes use it). ~5 LOC refactor, no behavior change.

**No Modified Endpoints:** `/api/documents/upload` is unchanged. The agent-side `read_org_document` tool is unchanged (still available for raw-byte sub-agent use cases).

**No Data Model Changes:** writes the same `parsed_documents` Firestore documents the upload route writes. `sourceUrl` already exists ([upload.py:159](../../../backend/tools/documents/upload.py#L159)) — the dedup query reads it.

### Frontend Changes

**Modified Components:**

- `frontend/src/app/chat/[...path]/page.tsx:660-665` (GCSFileBrowser `onPick`) — replace the synthetic chat message with:

  ```typescript
  onPick={async (bucket, objectName, label) => {
    const res = await fetchWithAuth("/api/proxy/api/documents/import-by-reference", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bucket, object: objectName, skillId }),
    });
    if (!res.ok) { /* surface existing upload-failure toast */ return; }
    const doc = await res.json();
    // mount in workbench identical to upload completion
    onDocumentParsed(doc);
  }}
  ```

- `frontend/src/app/chat/[...path]/page.tsx:796-807` (`onPickExample`) — identical treatment, same endpoint, same workbench mount.

- `frontend/src/components/chat/SkillExamplesPicker.tsx` — add a per-card loading state while the POST is in flight (~3s typical). Hardly needed but cheap insurance.

**No New Components:** the workbench mount path already exists for uploads (`onDocumentParsed` handler in `page.tsx`) — both rewires plug into it.

**State Management:** no new state. The picker is stateless above clicks; the chat page already tracks `includedDocIds`.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST | `/api/documents/import-by-reference` | Parse a GCS-resident file by reference; identical response shape to `/upload`. | No (additive) |

**Request body:**

```json
{ "bucket": "multivac-acme-energy-bucket", "object": "PPAs/longform/example-A.pdf", "skillId": "<uuid>" }
```

**Response (200):** `ParsedDocumentResponse` — same shape as `/api/documents/upload`. ([upload.py:85-94](../../../backend/tools/documents/upload.py#L85-L94))

**Error responses:**
- `401` — missing/invalid Firebase Bearer
- `403` — `welcome.bucket_browser` does not authorise this bucket for this skill (defer enforcement to v2; v1 trusts the SA-proxy path the existing M4 list endpoint already uses)
- `422` — AILANG Parse returned `failed`; `parseError` in detail
- `500` — unexpected GCS read failure (handled by existing `_run_parse` try/except, surfaced as `parseError`)

### Cache Layers and Cost Model

Three tiers — fastest to slowest — and what they cost:

| Tier | Where | Scope | Latency | AI-credit cost | Survives |
|------|-------|-------|---------|----------------|----------|
| L1 — in-memory parse cache | [ailang_parse.py:88-110](../../../backend/tools/documents/ailang_parse.py#L88-L110), keyed on `gs://` URL | Single Cloud Run revision, 1hr TTL | ~50ms | 0 | until Cloud Run cold-start or 1hr |
| L2 — Firestore `parsed_documents` per-user | This doc's dedup: `(userId, sourceUrl)` | Per-user, durable | ~200ms | 0 | forever (until doc deleted) |
| L3 — fresh AILANG Parse | Calls AILANG Parse API | None | ~3s (deterministic) / ~5-15s (AI) | **PDF/scanned: full Gemini-Vision per page**; DOCX/PPTX/XLSX/etc.: zero | n/a (writes to L2) |

**Mark's question 1 ("second time it caches?"):** Yes — second click by the **same user** is an L2 hit, ≤200ms, zero AI credits. Re-clicks within an hour from the same revision skip L2 and hit L1 directly via the upstream `_run_parse`.

**Mark's question 2 ("PDF routes — still going via docparse AI credits?"):** Yes. AILANG Parse's `DETERMINISTIC_EXTENSIONS` set today (verified [ailang_parse.py:48-62](../../../backend/tools/documents/ailang_parse.py#L48-L62)) is `{.docx, .pptx, .xlsx, .odt, .odp, .ods, .epub, .eml, .mbox, .html, .htm, .md, .csv}`. **PDF is not in it** — every fresh PDF parse calls AILANG's AI extraction (Gemini Vision per page). Cost scales with `pages × users` for shared example PPAs because L2 is per-user.

For the 5 ONE example PPAs (~30 pages each) × N users:
- Today's design: 5 × 30 × N = 150N page-credits (worst case)
- With L4 (below): 5 × 30 = 150 page-credits, **flat regardless of N**

### Cache Layer L4 — Shared Sentinel-User Pre-parse (in scope for 4.5.1)

To avoid the per-user re-parse cost on shared examples, this doc adds a **shared dedup tier** using a sentinel user pattern. Minimal change, ~15 LOC:

1. Pre-parse the 5 PPAs (and any future `welcome.example_documents`) once as `userId = PLATFORM_OWNER_UID` via a Cloud Build deploy step (`aiplatform docs import-from-bucket --as-platform`).
2. The dedup query order becomes:
   1. `(userId=self, sourceUrl=X)` → if hit, return as-is (user has their own copy)
   2. `(userId=PLATFORM_OWNER_UID, sourceUrl=X)` → if hit, **clone the blocks into a per-user record** (single Firestore write, no parse) and return
   3. Fall through to fresh `_run_parse`

That keeps the per-user record model intact (each user still owns their `parsed_documents/{docId}` row with their own `editedBlocks`, folder, included state) while paying the parse cost exactly once per shared file.

**What scope ships in 4.5.1:**
- The dedup-order change in `import_by_reference.py` (~10 LOC: try self, then `PLATFORM_OWNER_UID`, then parse)
- A `--as-platform` flag on the CLI subcommand (~5 LOC: substitutes `PLATFORM_OWNER_UID` for the user's uid at call time)
- A Cloud Build step (or one-shot manual command) that pre-parses the 5 ONE example PPAs on next deploy — same idempotent pattern as `seed-platform-skills`

Pre-warming the 5 example PPAs against the deployed backend before Fri demo collapses the first-click latency for Mark + every subsequent user from ~5s to ~500ms (Firestore clone) and zeroes the AI-credit bill for examples.

### Architecture Diagram

```
User clicks example PPA in SkillExamplesPicker (or file in GCSFileBrowser)
      │
      ▼
Frontend: fetchWithAuth POST /api/proxy/api/documents/import-by-reference
          body = { bucket, object, skillId }
      │
      ▼
Backend route (new) — backend/tools/documents/import_by_reference.py
      │
      ├──► L2: Firestore parsed_documents WHERE userId=self AND sourceUrl=?
      │         └──► HIT → return existing record (~200ms, $0)
      │
      ├──► L4: Firestore parsed_documents WHERE userId=PLATFORM_OWNER_UID AND sourceUrl=?
      │         └──► HIT → clone blocks into new per-user record (~300ms, $0)
      │
      ├──► L3/L1: _run_parse(gs_url)  [reused from upload.py:107]
      │         ├──► L1 in-memory cache hit (~50ms, $0)
      │         └──► fresh AILANG Parse:
      │                  • DOCX/PPTX/XLSX/etc.: deterministic, $0
      │                  • PDF: Gemini Vision per page, ~$0.001/page
      │
      └──► _store_document(...)  [reused from upload.py:141]
                └──► Firestore: parsed_documents/{doc_id} = {...blocks, sourceUrl, userId, ...}
      │
      ▼
Frontend: mount returned doc in Workbench (existing onDocumentParsed path)
```

### CLI Surface

Per [local-dev-cli.md](../v6.1.0/local-dev-cli.md) §5b-bis: this introduces a developer-facing operation (parse a bucket file outside the UI) that today requires a curl + Firebase token + JSON body. Replace with:

```bash
aiplatform docs import-from-bucket \
  --bucket multivac-acme-energy-bucket \
  --object PPAs/longform/example-A.pdf \
  --skill-id one-ppa-expert
```

Adds a new `import-from-bucket` subcommand under the existing `aiplatform docs` group. ~30 LOC: Click subcommand + httpx POST + a JSON pretty-print of the response. One unit test asserting the body shape matches `ImportByReferenceRequest`.

## Implementation Plan

Single-day sprint. Sequential — no parallel tracks needed.

| M | Task | Est | Files | Tests |
|---|------|-----|-------|-------|
| M1 | Extract `_to_response` helper in upload.py | 0.05d | `backend/tools/documents/upload.py` | covered by existing `test_upload.py` |
| M2 | New `import_by_reference.py` route (self dedup → L4 sentinel dedup-and-clone → fresh parse) + router include | 0.2d | `backend/tools/documents/import_by_reference.py`, `backend/fast_api_app.py` | `backend/tests/api_tests/test_import_by_reference.py` — 4 pytest: self-dedup hit, sentinel-dedup clones to per-user, fresh parse, bad gs:// URL |
| M3 | Frontend rewire (picker + browser) | 0.15d | `frontend/src/app/chat/[...path]/page.tsx`, `frontend/src/components/chat/SkillExamplesPicker.tsx` | `frontend/src/components/chat/__tests__/SkillExamplesPicker.test.tsx` — 2 vitest: click triggers POST + workbench mount via `onDocumentParsed` |
| M4 | CLI subcommand incl. `--as-platform` flag | 0.15d | `cli/aiplatform/docs.py` | `cli/tests/test_docs_import_from_bucket.py` — assert `--as-platform` writes as `PLATFORM_OWNER_UID` |
| M5 | One-shot pre-parse of the 5 ONE example PPAs against deployed backend (run before Fri demo) | 0.1d | manual `aiplatform docs import-from-bucket --as-platform` × 5 | acceptance: 5 records exist in `parsed_documents` with `userId=PLATFORM_OWNER_UID`; first-time-user click latency ≤500ms in Cloud Run logs |
| M6 | Live verify against `one-ppa-expert` example PPAs | 0.05d | — | `aitana-frontend-verify` skill: click each example, confirm workbench mount + Firestore record creation + ≤500ms second-user-first-click |

Total: 0.75d. Cut line: drop M4 (CLI) if Thu runs hot — M5 then becomes a manual curl against the deployed admin endpoint. M3 is non-negotiable (the demo gate).

## Migration

**Data Migration:** none. Writes to the existing `parsed_documents` collection with no schema changes; coexists with upload-sourced records.

**Feature Flags:** none. Additive route, no behavior change for existing flows. The frontend rewire is the cutover — once it lands, picker/browser clicks call the new endpoint instead of `sendMessage`.

**Rollback Plan:** revert the frontend rewire commit (single PR). The new backend route stays harmless; the picker/browser fall back to the synthetic-chat hack. No data corruption risk — the route only writes new Firestore documents, never modifies existing ones.

## Testing Strategy

**Backend (pytest):**
- `backend/tests/api_tests/test_import_by_reference.py`:
  - `test_dedup_hit` — pre-seed a `parsed_documents` record with matching `(userId, sourceUrl)`; assert route returns it in ≤200ms without calling `_run_parse`
  - `test_fresh_parse` — mock `_run_parse` to return canned blocks; assert `_store_document` was called and response shape matches `ParsedDocumentResponse`
  - `test_parse_failure_returns_422` — mock `_run_parse` to return `("failed", [], 0, "test error")`; assert 422 with `parseError` in detail

**Frontend (vitest):**
- `frontend/src/components/chat/__tests__/SkillExamplesPicker.test.tsx`:
  - `test_click_calls_import_endpoint` — mock `fetchWithAuth`; assert POST to `/api/proxy/api/documents/import-by-reference` with expected body
  - `test_success_invokes_on_document_parsed` — mock 200 response; assert workbench-mount callback fires with returned doc

**E2E (manual):** `aitana-frontend-verify` skill — open `https://your-service-url.example/chat/@aitana-platform/one-ppa-expert`, click each of the 5 example PPAs, confirm:
1. Workbench Document tab populates with parsed blocks
2. Chat input shows the "Will process: example-X.pdf" InContextBadge
3. First chat turn references the parsed content (e.g. "What's the price floor?" → cites a block)
4. Re-clicking the same example returns instantly (network panel shows ≤200ms response)

**CLI:** `cli/tests/test_docs_import_from_bucket.py` — assert the subcommand parses flags and POSTs the right body to the backend; mock the HTTP response.

## Success Criteria

- [ ] `POST /api/documents/import-by-reference` returns 200 with `ParsedDocumentResponse` for a valid `(bucket, object)` pair
- [ ] Dedup hit returns ≤200ms P95 (no `_run_parse` call); in-memory parse cache hit returns ≤300ms P95 on a fresh user; cold parse ≤5s P95 for a 30-page PPA
- [ ] All 5 `one-ppa-expert.welcome.example_documents` import + render in the workbench end-to-end
- [ ] All `multivac-acme-energy-bucket/PPAs/` files clickable via the sidebar bucket browser parse + mount the same way
- [ ] Re-clicking an already-imported file shows the existing workbench tab (or re-mounts the same `docId`) in ≤500ms
- [ ] Zero "parse failed" UX events in `.dev-logs/backend.log` during the onboarding gesture
- [ ] `aiplatform docs import-from-bucket` round-trips against `make dev` backend and prints `parseStatus: parsed`
- [ ] 3 backend pytest pass; 2 frontend vitest pass; 1 CLI unit test passes
- [ ] One log line `import_by_reference: dedup_hit OR fresh_parse` per request, visible in Cloud Trace

## Security Considerations

- **No new trust boundaries:** the SA-credentialed backend proxy is the same one [4.5 M4's `GET /api/buckets/{name}/list`](../../../backend/buckets/routes.py) uses. The frontend never sees bucket credentials.
- **Bucket access policy (v1):** trust `welcome.bucket_browser` whitelisting as the access policy — same as the M4 list endpoint. A user can only request `import-by-reference` for a bucket the picker/browser surfaced to them; both surfaces are gated by skill access (tagged `["ONE", "aitana-admin"]` for the ONE skills, etc.). Open question for v2: should the route do an explicit policy check (re-read `skill.welcome.bucket_browser` and reject buckets not in the whitelist)? Decision: defer — same trust model as M4, no regression. Add the check if/when M4 adds one.
- **Path traversal:** the `object` field reaches GCS as a literal path. GCS treats `..` as a literal segment (no traversal vulnerability), but a malicious caller could enumerate other buckets they can't see via the browser. Mitigated by the same `welcome.bucket_browser` whitelist above. Add explicit validation if a future user-supplied `bucket` field bypasses the whitelist.
- **Data egress:** none. Parsed blocks stay in Firestore (inside the GCP project edge). AILANG Parse runs in-process. No third-party SaaS touched.
- **Prompt injection:** the document content reaches the model via the existing `build_document_context` path — same surface as user uploads. No new injection vector.

## Related Documents

- [skill-onboarding.md](skill-onboarding.md) — v6.4.0 4.5; defines `welcome.example_documents` + `welcome.bucket_browser` schemas this doc consumes
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance convention
- [internal-app-shell-port.md](internal-app-shell-port.md) — v6.4.0 4.3; ChatShell + workbench primitives the parsed doc mounts into

## Future: Batch Parsing Whole Buckets (v6.5 candidate)

**Mark's question 3 ("eventually we will look at batch parsing all documents — where will that data be stored?"):** the 4.5.1 L4 sentinel-user pattern scales fine to dozens of pre-warmed examples. Past ~hundreds of docs (e.g. the full Contracts library at `gs://multivac-acme-energy-bucket/PPAs/` — 247 indexed files today, growing), the per-user clone-on-read step gets wasteful — every user gets their own copy of identical block lists with no edits.

**The v6.5 evolution (not in scope for 4.5.1, sketched here for continuity):** split `parsed_documents` into two collections:

| Collection | Key | Contains | Lifetime |
|------------|-----|----------|----------|
| `parsed_blocks` | hash of `gs://` URL | `blocks[]`, `parsedMs`, `sourceFormat`, `parsedAt` — the *content* of a parse | Shared across all users; deleted only when the source GCS object is deleted |
| `parsed_documents` | UUID, scoped to `userId` | per-user metadata: `folderId`, `editedBlocks`, `includedInChat`, `originalFilename`, **`blocksRef`** (pointer to `parsed_blocks`) | Per-user; deleted when user removes from their library |

`blocksRef` lets the per-user record point at a shared block list instead of cloning it. Batch jobs (Cloud Run Job triggered by `aiplatform docs batch-parse --bucket X --prefix Y`) write straight to `parsed_blocks` with no `userId` involvement. Per-user records become tiny (~1 KB) and creation-on-click is just a Firestore write with a reference — no block copying.

**Why not do this in 4.5.1:**
- Bigger refactor (touches `upload.py`, the workspace render path, doc-listing endpoints, the artifact-loader callback's orphan probe)
- Demo-week scope discipline — the sentinel-user pattern hits 100% of the demo cost win for the 5 examples
- The seam (`blocksRef` field on `parsed_documents`) is forward-compatible: 4.5.1 records can be migrated by writing a `blocksRef` field pointing at a `parsed_blocks` copy of their inlined blocks, then later removing the inline `blocks` field. No breaking change for existing records.

**Triggering batch parses (v6.5):**
- CLI: `aiplatform docs batch-parse --bucket multivac-acme-energy-bucket --prefix PPAs/` — kicks off a Cloud Run Job that walks the bucket and parses every supported file
- The job writes to `parsed_blocks` (idempotent: skip files whose hash already exists) and reports cost (page counts × $/page for PDFs)
- A nightly Cloud Scheduler job can re-scan declared `welcome.bucket_browser` buckets to pick up new files

Until 4.5.1 ships and the L4 sentinel pattern proves itself, the v6.5 split is a sketch — actual schema will be re-justified in its own design doc.

## Open Questions

1. **Explicit bucket-whitelist enforcement on the route?** v1 trusts the `welcome.bucket_browser` whitelist enforced at the UI layer + the M4 list endpoint's identical trust model. v2 may add a server-side cross-check that the `(skillId, bucket)` pair is declared in the skill's welcome config. Not a v1 blocker since the SA-proxy still gates *which* buckets the backend can read.
2. **Pre-parse trigger: Cloud Build deploy step vs. lazy on-demand?** The L4 sentinel pre-parse can run either as a `seed-platform-skills`-style step on every deploy (idempotent, cost ~5 PDFs × ~$0.05 = $0.25 per deploy if rerun for some reason) or on demand via `aiplatform docs import-from-bucket --as-platform`. v1: ship the CLI flag, document a manual one-shot for the Fri demo. v2: add a Cloud Build step gated on skills that declare `welcome.example_documents`.
3. **L4 clone vs. L4 reference (no clone)?** Cloning blocks into a per-user record on L4 hit keeps `editedBlocks` semantics intact (user edits don't bleed across users) but wastes ~50-200 KB per user per shared doc. Alternative: skip the clone, return the sentinel record directly with `userId` overridden in the response — but `editedBlocks` would have to move to a sidecar collection. v1 ships the clone (simpler, correct); v6.5's `parsed_blocks` split (above) is the proper long-term answer.

## Implementation Report

_(To be filled after sprint completes — same format as 4.5's implementation report when this doc is moved to `implemented/`.)_
