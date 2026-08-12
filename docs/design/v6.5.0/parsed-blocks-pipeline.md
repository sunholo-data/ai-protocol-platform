# Parsed Blocks Pipeline — Batch Document Parsing via Multivac Chunker

**Status**: Planned
**Priority**: P1 (Medium — pays off when example libraries grow past ~20 docs)
**Estimated**: 3 days (1.5d backend incl. PubSub subscriber + new Firestore collection; 0.5d multivac chunker addition; 0.5d CLI + Cloud Run Job for backfill; 0.5d tests + observability)
**Scope**: Fullstack + cross-repo (this repo + `multivac-system-services` + Sunholo infra terraform)
**Dependencies**: v6.4.0 4.5.1 [document-import-by-reference.md](../v6.4.0/document-import-by-reference.md) shipped, multivac chunker AILANG Parse migration shipped 2026-06-03 (commit `e8d3ece`)
**Sprint Key**: PARSED-BLOCKS
**Created**: 2026-06-10
**Last Updated**: 2026-06-10

## Problem Statement

v6.4.0 4.5.1 ships per-user dedup + a shared sentinel-user (`PLATFORM_OWNER_UID`) L4 cache so the 5 ONE example PPAs parse once across all users. That scales fine to dozens of pre-warmed examples. Past that scale, three problems compound:

**Problem 1 — Wasted parse work for new bucket arrivals.** When ONE drops a new PPA into `gs://multivac-acme-energy-bucket/PPAs/`, the platform doesn't know about it until a user clicks it. The first user pays the AILANG Parse latency + AI credits; the L4 sentinel pattern only helps if someone explicitly pre-parses it via `--as-platform`.

**Problem 2 — The multivac chunker is already doing this work.** As of `e8d3ece` (2026-06-03), every GCS upload to multivac-monitored buckets fires `app-to-pubsub-chunk` → chunker → AILANG Parse → publishes to `chunker-to-aisearch` (currently only the aisearch service consumes it, to load Discovery Engine datastores). The parse already happened. Aitana platform paying for the same parse a second time when a user clicks the file is pure waste.

**Problem 3 — Per-user record bloat.** The 4.5.1 sentinel-user pattern clones blocks into a per-user record on every first click. For a 30-page PPA that's ~50-200 KB of duplicated Firestore data per user. Acceptable for 5 examples; wasteful for 247 indexed ONE PPAs × hundreds of ONE users.

**Current State:**
- Multivac chunker publishes parsed output as **markdown** to `chunker-to-aisearch` for Discovery Engine, and **per-chunk embed payloads** to `chunk-to-pubsub-embed` for vector stores. Neither preserves the original block ADT — both are lossy transforms.
- v6 Aitana has no consumer of either topic — it parses independently via `_run_parse` ([upload.py:107](../../../backend/tools/documents/upload.py#L107)) on click.
- `parsed_documents` Firestore collection holds per-user records with inlined blocks — no shared content layer.

**Impact:**
- **Cost**: PDF AI-credit charges duplicated between multivac aisearch ingestion + Aitana on-demand parse. At 30 pages × $0.001/page × 247 PPAs = $7.41 wasted per full bucket re-parse on Aitana side, on top of the multivac aisearch charge that already paid.
- **Latency**: first-click for a new ONE PPA is ~5s today (4.5.1 sentinel doesn't auto-warm on bucket arrivals). With this pipeline, first-click is L4-hit ≤500ms because the chunker pre-warmed `parsed_blocks` at upload time.
- **Storage**: 247 PPAs × 100 KB blocks × hundreds of users = GB-scale Firestore duplication. Shared content avoids this.

## Goals

**Primary Goal:** Every parse done by the multivac chunker for any GCS bucket lands once in a shared `parsed_blocks` Firestore collection in the Aitana project, keyed by GCS source URL. Aitana's `import-by-reference` route reads from this shared collection before falling through to a fresh parse.

**Success Metrics:**
- Zero duplicate AILANG Parse calls between multivac chunker + Aitana for any document already ingested by multivac
- First-click latency for a multivac-ingested document ≤500ms P95 (Firestore reference lookup, no parse)
- 100% of files in `gs://multivac-acme-energy-bucket/PPAs/` retrievable from `parsed_blocks` after a one-shot backfill
- Per-user `parsed_documents` records shrink to ~1 KB (metadata + `blocksRef` only) instead of 50-200 KB (inlined blocks)
- New bucket arrivals propagate to `parsed_blocks` within ~30s of upload (matches existing chunker SLA for aisearch ingestion)

**Non-Goals:**
- Replacing the multivac chunker. Both pipelines coexist — aisearch keeps consuming `chunker-to-aisearch` (markdown for Discovery Engine), Aitana adds a new consumer for blocks.
- Rewriting AILANG Parse client integration on the Aitana side. The 4.5.1 `_run_parse` path remains the fallback for buckets not under multivac chunker management.
- Cross-tenant block sharing across customer GCP projects. Each customer's `parsed_blocks` collection lives in their own Aitana deployment's Firestore.
- A general "GCS event bus for v6". Scope is narrow: AILANG Parse output flowing from multivac to Aitana. Other PubSub integrations are out of scope.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | First-click for chunker-ingested docs collapses from ~5s to ≤500ms (Firestore lookup). No model latency on the cache-hit path. |
| 2 | EARNED TRUST | +1 | The Aitana platform now grounds every document the same way (same blocks, same `block_id`s, same `heading_path`) whether it came via user upload or bucket arrival. Citation paths flow identically. |
| 3 | SKILLS, NOT FEATURES | 0 | Surface-level invisible to users. Skills don't see this; the shared layer is plumbing. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Eliminates duplicate AILANG Parse calls for the same document — that's deterministic processing being reused properly instead of re-paying for it. |
| 5 | GRACEFUL DEGRADATION | +1 | Subscriber failure (PubSub backlog, schema mismatch, Firestore write fail) does not block aisearch ingestion — separate subscription. Aitana's `import-by-reference` route falls through to fresh parse if `parsed_blocks` lookup misses, identical to today's behaviour. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses existing PubSub fan-out pattern. New topic + subscription follows the exact same shape as the `chunker-to-aisearch` integration that proved itself in production 2026-06-03. No new protocol. |
| 7 | API FIRST | +1 | The shared `parsed_blocks` collection is read by `import-by-reference` (HTTP) and by the agent's `read_org_document` tool (replaces raw-bytes path with parsed blocks). Channel-agnostic — Telegram + CLI consume identically. |
| 8 | OBSERVABLE BY DEFAULT | +1 | New `aitana_parsed_blocks_writer` Cloud Run service emits OpenTelemetry spans per message (received → Firestore write). New BigQuery sink `parsed_blocks_archive` for audit (matches the `qna-to-pubsub-bq-archive` precedent the qna pipeline established). |
| 9 | SECURE BY CONSTRUCTION | +1 | Subscriber runs in Aitana's project with its SA identity; multivac chunker remains in multivac project. Topic crosses project boundary via PubSub IAM grant — explicit auditable seam. No raw bucket reads in the subscriber. Stays inside the trust boundary (Sunholo + Aitana are same trust zone per the Privacy Boundary table). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All cache + dedup logic lives in the backend. Frontend never knows whether blocks came from chunker pre-warm or fresh parse — identical `ParsedDocumentResponse`. |
| | **Net Score** | **+8** | Threshold: ≥ +4. Strong alignment — proceed. |

**Conflict Justifications:**
- None. No axiom scored -1.

## Standards Compliance Check

PubSub is the established Sunholo/multivac standard for inter-service async messaging (every existing topic: `app-to-pubsub-chunk`, `chunker-to-aisearch`, `chunk-to-pubsub-embed`, `qna-to-pubsub-bq-archive`). No custom protocol invented. Message body is plain JSON matching the AILANG Parse `Block` ADT shape already documented in [document-to-ai-pipeline.md](../v6.1.0/implemented/document-to-ai-pipeline.md#L145-L188).

## Design

### Overview

Add a third vectorstore destination type to the multivac chunker's `dispatch_parsed_doc` — `aitana_blocks` — which publishes the raw `Block` ADT (not a markdown-or-chunk lossy transform) to a new topic `chunker-to-aitana-blocks`. In Aitana, add an `aitana_parsed_blocks_writer` Cloud Run service that subscribes to that topic and writes to a new shared Firestore collection `parsed_blocks` keyed by GCS source URL hash. The `import-by-reference` route's L4 lookup ([4.5.1 design](../v6.4.0/document-import-by-reference.md#L209)) gains a third tier: try `parsed_blocks` first, then sentinel-user, then fresh parse. Per-user `parsed_documents` records gain a `blocksRef` field pointing at the shared row instead of inlining blocks.

### Multivac Chunker Changes (cross-repo: `multivac-system-services`)

**New code** in `chunker/dispatch.py`:

```python
AITANA_BLOCKS_TOPIC = "chunker-to-aitana-blocks"

def build_aitana_blocks_payload(
    parse_result: ParseResult,
    base_metadata: dict,
    vector_name: str,
    memory_name: str,
) -> dict:
    """Preserve the Block ADT verbatim — no markdown synth, no per-chunk split.

    Aitana's parsed_blocks_writer wants the original AILANG Parse output so
    workspace renderers + block_id citations + heading_path navigation all
    work identically to user-uploaded docs.
    """
    return {
        "doc_id": base_metadata.get("doc_id"),
        "source_url": base_metadata.get("source"),  # gs://bucket/path/...
        "filename": base_metadata.get("filename"),
        "source_format": parse_result.source_format,
        "vector_name": vector_name,
        "memory_name": memory_name,
        "event_time": base_metadata.get("eventTime"),
        "blocks": [b.to_dict() for b in parse_result.blocks],  # the Block ADT
        "doc_metadata": {
            "title": parse_result.title,
            "author": parse_result.author,
            "page_count": parse_result.page_count,
            "n_headings": parse_result.n_headings,
            "n_tables": parse_result.n_tables,
            "n_images": parse_result.n_images,
            "n_changes": parse_result.n_changes,
        },
    }
```

**Modified** `dispatch_parsed_doc` (lines 88-112 in [dispatch.py](https://github.com/sunholo-data/multivac-system-services/blob/main/chunker/dispatch.py)) — add a branch:

```python
elif vectorstore in AITANA_BLOCKS_VECTORSTORES:
    payload = build_aitana_blocks_payload(parse_result, base_metadata, vector_name, memory_name)
    publisher = publisher_factory(AITANA_BLOCKS_TOPIC)
    # Size guard: PubSub max ~10 MiB. For oversized docs, stage to GCS + send pointer.
    body = json.dumps(payload)
    if len(body) > 9 * 1024 * 1024:
        body = _stage_to_gcs_and_pointer(payload, vector_name)
    publisher.publish_message(body)
```

Constant `AITANA_BLOCKS_VECTORSTORES = {"aitana_blocks", "aitana_parsed_blocks"}` — the per-memory `vectorstore:` config string opts a memory in. Reuses the same fan-out config-driven pattern the existing DE + embed branches use.

**No multivac-side compatibility risk:** existing memories don't declare `vectorstore: aitana_blocks`, so this is dead code until a VAC config opts in. Same shape that landed `e8d3ece`'s migration off Unstructured.

### Multivac Infra Changes (`multivac-aitana/infrastructure`)

**Add to** `infrastructure/environments/common/terraform.tfvars`:

```hcl
pubsub_topics = [
  # ... existing topics
  {
    name    = "chunker-to-aitana-blocks"
    project = "your-deploy-project-id"  # Aitana platform consumes
    iam = {
      # Multivac chunker SA publishes
      "roles/pubsub.publisher" = ["multivac-chunker@<multivac-project>.iam.gserviceaccount.com"]
      # Aitana subscriber SA reads
      "roles/pubsub.subscriber" = ["aitana-parsed-blocks-writer@<aitana-project>.iam.gserviceaccount.com"]
    }
  }
]
```

**Add to** `chunker/memory.tfvars` for ONE VAC (`vacConfig("memory")`):

```yaml
memory:
  - aitana_blocks:
      vectorstore: aitana_blocks
      read_only: false
      project_id: your-project-id-prod  # destination project for the topic
```

### Aitana Backend Changes

**New module** `backend/documents/parsed_blocks_writer.py` (deployed as a separate Cloud Run service or co-located on the main backend with a dedicated PubSub push endpoint):

```python
@router.post("/api/internal/parsed-blocks/ingest")
async def ingest_parsed_blocks(request: Request) -> dict:
    """PubSub push endpoint: receive AILANG-parsed block payloads from the
    multivac chunker and write to the shared parsed_blocks Firestore collection.

    Caller auth: Cloud Run invoker IAM gates this endpoint. PubSub's push
    subscription mints an OIDC token under
    aitana-parsed-blocks-writer@<project>.iam.gserviceaccount.com; the
    endpoint verifies the OIDC issuer + the SA is in PARSED_BLOCKS_ALLOWED_SAS.
    """
    _assert_caller_is_service_account(request, allowlist_env="PARSED_BLOCKS_ALLOWED_SAS")
    envelope = await request.json()
    payload = json.loads(base64.b64decode(envelope["message"]["data"]))

    source_url = payload["source_url"]  # gs://bucket/path/file.pdf
    blocks_id = _hash_source_url(source_url)  # sha256 → 16 hex chars

    set_document(
        "parsed_blocks",
        blocks_id,
        {
            "sourceUrl": source_url,
            "filename": payload["filename"],
            "sourceFormat": payload["source_format"],
            "blocks": payload["blocks"],
            "blockCount": len(payload["blocks"]),
            "docMetadata": payload["doc_metadata"],
            "vectorName": payload["vector_name"],
            "ingestedAt": payload["event_time"],
            "ingestedBy": "multivac-chunker",
            "updatedAt": now_iso(),
        },
    )
    return {"status": "ingested", "blocksId": blocks_id}
```

**Modified** `backend/tools/documents/import_by_reference.py` (the 4.5.1 route) — extend the cache cascade:

```python
gs_url = f"gs://{req.bucket}/{req.object}"
blocks_id = _hash_source_url(gs_url)

# L2: per-user dedup
existing = query_documents("parsed_documents",
    filters=[("userId", "==", user.uid), ("sourceUrl", "==", gs_url)], limit=1)
if existing:
    return _to_response(existing[0])

# NEW L5: shared parsed_blocks (chunker-pre-warmed or batch-backfilled)
shared = get_document("parsed_blocks", blocks_id)
if shared:
    # Create per-user metadata record pointing at shared blocks
    doc_id = str(uuid.uuid4())
    set_document("parsed_documents", doc_id, {
        "userId": user.uid,
        "skillId": req.skill_id,
        "sourceUrl": gs_url,
        "blocksRef": blocks_id,        # ← reference, not inline
        "originalFilename": shared["filename"],
        "parseStatus": "parsed",
        "blockCount": shared["blockCount"],
        # editedBlocks stays per-user; never crosses the reference
        "editedBlocks": {},
        "createdAt": now_iso(),
    })
    return _to_response_for_new(doc_id, "parsed", shared["blocks"], req.object)

# L4: sentinel-user fallback (4.5.1)
sentinel = query_documents("parsed_documents",
    filters=[("userId", "==", PLATFORM_OWNER_UID), ("sourceUrl", "==", gs_url)], limit=1)
if sentinel:
    return _clone_to_user(sentinel[0], user.uid)

# L3: fresh parse
return await _fresh_parse_and_store(gs_url, user, req.skill_id)
```

**Backwards-compat reader** — `parsed_documents` records may now have *either* an inlined `blocks` array (legacy, 4.5.1-and-before) OR a `blocksRef` pointer. The workspace render path and `build_document_context` need a one-line helper:

```python
def get_blocks(doc: dict) -> list[dict]:
    if doc.get("blocksRef"):
        return get_document("parsed_blocks", doc["blocksRef"])["blocks"]
    return doc.get("blocks", [])
```

### Aitana Backend — Backfill Job

**New** `backend/jobs/batch_parse.py` — Cloud Run Job (not service) that walks a bucket prefix and synthesises GCS-finalized PubSub messages to `app-to-pubsub-chunk`, letting the existing multivac chunker do the heavy lifting:

```python
def batch_parse_bucket(bucket: str, prefix: str, vector_name: str) -> dict:
    """Publish a synthetic OBJECT_FINALIZE event to app-to-pubsub-chunk for
    every object under gs://{bucket}/{prefix}, triggering the multivac chunker's
    full parse + dispatch flow as if each file had just been uploaded.

    Idempotent: chunker writes to parsed_blocks by source URL hash; re-runs
    overwrite (or skip via existence check).
    """
    storage_client = google.cloud.storage.Client()
    blobs = storage_client.list_blobs(bucket, prefix=prefix)
    publisher = PubSubManager(vector_name, pubsub_topic="app-to-pubsub-chunk")
    count = 0
    for blob in blobs:
        if blob.name.endswith("/"):
            continue  # skip prefix markers
        if get_document("parsed_blocks", _hash_source_url(f"gs://{bucket}/{blob.name}")):
            continue  # skip already-ingested
        attributes = {
            "bucketId": bucket,
            "objectId": blob.name,
            "eventType": "OBJECT_FINALIZE",
            "namespace": vector_name,
            "synthetic": "aitana-batch-parse",  # tag for chunker logs
        }
        publisher.publish_message("", attributes=attributes)
        count += 1
    return {"bucket": bucket, "prefix": prefix, "published": count}
```

Cloud Run Job spec (~30 LOC terraform): 1 CPU, 1 GiB, max 1 instance, 30-min timeout. Triggered manually via CLI (below) or by Cloud Scheduler nightly for declared `welcome.bucket_browser` buckets.

### CLI Surface

Per [local-dev-cli.md](../v6.1.0/local-dev-cli.md):

```bash
# One-shot backfill: walk a bucket, kick the chunker for every file
aiplatform docs batch-parse \
  --bucket multivac-acme-energy-bucket \
  --prefix PPAs/longform/ \
  --vector-name aitana

# Dry-run: list files that would be processed without publishing
aiplatform docs batch-parse --bucket X --prefix Y --dry-run

# Status check: how many parsed_blocks records exist for a bucket
aiplatform docs blocks-status --bucket X --prefix Y

# Lookup a single block by source URL
aiplatform docs blocks-show --gs-url gs://bucket/path/file.pdf
```

~80 LOC of Click + httpx. New `aiplatform docs` subgroup or extend the existing one from 4.5's `aiplatform examples`.

### Architecture Diagram

```
EXISTING (unchanged):
GCS upload → app-to-pubsub-chunk → chunker → AILANG Parse
                                       │
                                       ├──► chunker-to-aisearch ──► aisearch ──► Discovery Engine
                                       │
                                       └──► chunk-to-pubsub-embed ──► embedder ──► vector store

NEW (this design doc):
                                       │
                                       └──► chunker-to-aitana-blocks ──► aitana-parsed-blocks-writer
                                                                              │
                                                                              ▼
                                                                    Firestore: parsed_blocks/{sha256(gs_url)}
                                                                              │
                                                                              │ referenced by blocksRef
                                                                              ▼
                                                                    Firestore: parsed_documents/{user_doc}

NEW BACKFILL PATH:
aiplatform docs batch-parse → list bucket → publish synthetic OBJECT_FINALIZE messages
                                                  to app-to-pubsub-chunk → existing chunker flow runs
```

### Data Model

**New collection** `parsed_blocks` (Aitana Firestore):

| Field | Type | Notes |
|-------|------|-------|
| document ID | string | sha256(sourceUrl)[0:16] — stable, deterministic |
| sourceUrl | string | gs://bucket/path/file.pdf — indexed |
| filename | string | leaf name |
| sourceFormat | string | docx, pdf, xlsx, etc. |
| blocks | array<dict> | the original Block ADT — same shape as today's inlined `parsed_documents.blocks` |
| blockCount | int | precomputed for stats |
| docMetadata | dict | title, author, page_count, n_tables, n_images, n_changes |
| vectorName | string | multivac VAC name (provenance) |
| ingestedAt | timestamp | from chunker event_time |
| ingestedBy | string | "multivac-chunker" or "aitana-batch-parse" |
| updatedAt | timestamp | last write |

**Modified collection** `parsed_documents` — adds optional `blocksRef: string` pointing at `parsed_blocks/{id}`. When set, `blocks` field is empty (saves storage). Existing records keep inlined `blocks` (no migration required for v1; lazy migration on next user touch).

## CLI Surface

(See "CLI Surface" subsection above under Design.)

Adds:
- `aiplatform docs batch-parse --bucket X --prefix Y --vector-name V` (+ `--dry-run`)
- `aiplatform docs blocks-status --bucket X [--prefix Y]`
- `aiplatform docs blocks-show --gs-url <gs://...>`

## Implementation Plan

| M | Task | Est | Repo | Files | Tests |
|---|------|-----|------|-------|-------|
| M1 | Add `aitana_blocks` vectorstore branch + `build_aitana_blocks_payload` to chunker | 0.25d | multivac-system-services | `chunker/dispatch.py` | `chunker/tests/test_dispatch_aitana_blocks.py` — assert payload shape + topic |
| M2 | Terraform: new topic `chunker-to-aitana-blocks` + IAM grants | 0.25d | multivac-aitana | `infrastructure/environments/common/terraform.tfvars` | `terraform plan` review |
| M3 | New Aitana subscriber endpoint `/api/internal/parsed-blocks/ingest` + auth | 0.5d | platform | `backend/documents/parsed_blocks_writer.py`, `backend/admin/auth.py` (extend allowlist env) | `backend/tests/api_tests/test_parsed_blocks_ingest.py` — 4 pytest: valid push, bad OIDC, oversized payload, duplicate ingest |
| M4 | Modify `import_by_reference` to add L5 shared lookup + `blocksRef` writeback; `get_blocks` reader helper | 0.5d | platform | `backend/tools/documents/import_by_reference.py`, `backend/tools/documents/upload.py` | `backend/tests/api_tests/test_import_by_reference.py` — add 2 pytest: L5 hit creates blocksRef record, blocksRef read flows through `get_blocks` |
| M5 | Batch-parse Cloud Run Job + terraform | 0.5d | platform + multivac-aitana | `backend/jobs/batch_parse.py`, `infrastructure/.../cloudrun_jobs.tfvars` | `backend/tests/jobs/test_batch_parse.py` |
| M6 | CLI: `aiplatform docs batch-parse / blocks-status / blocks-show` | 0.25d | platform | `cli/aiplatform/docs.py` | `cli/tests/test_docs_batch_parse.py` |
| M7 | One-shot backfill of `gs://multivac-acme-energy-bucket/PPAs/` (247 files) — production validation | 0.25d | — | manual: `aiplatform docs batch-parse --bucket ... --prefix PPAs/` | acceptance: 247 records in `parsed_blocks` within ~30 min; first-click latency ≤500ms for any file |
| M8 | Observability: OTEL spans on writer, BigQuery sink `parsed_blocks_archive` mirroring qna pattern | 0.5d | platform + multivac-aitana | `backend/documents/parsed_blocks_writer.py`, `infrastructure/.../bigquery.tfvars` | `aiplatform monitoring blocks-pipeline-health` smoke |

Total: 3d. Two cross-repo PRs (multivac-system-services M1, multivac-aitana M2+M5+M8 infra), platform main PR covers M3+M4+M6+M7.

**Critical path order:** M1 → M2 → M3 (subscriber stands up against a working topic) → M4 → M5 → M6 → M7 → M8. Cannot parallelise M1+M3 because the topic doesn't exist until M2.

Cut line: drop M5+M6+M7 (backfill job + CLI + production backfill) if the multivac-side work runs hot — the inbound subscriber alone covers all *new* arrivals; backfill can be a v6.5.1 follow-up.

## Migration

**Existing `parsed_documents` records (inlined `blocks`)**: no migration on day 1. The `get_blocks(doc)` helper transparently returns either `doc["blocks"]` (legacy) or `parsed_blocks[doc["blocksRef"]]["blocks"]` (new). Records remain valid forever.

**Lazy backfill** (optional, v6.5.1): a one-shot script can walk `parsed_documents`, hash each `sourceUrl`, write to `parsed_blocks` if absent, then null out `blocks` and set `blocksRef`. ~50 LOC; not required for correctness.

**Multivac chunker rollout**:
1. Deploy multivac M1 (new branch in dispatch.py) — dead code until any VAC opts in via `memory.aitana_blocks`
2. Deploy Aitana M2-M3 (terraform topic + subscriber)
3. Update ONE VAC's `memory.tfvars` to add `aitana_blocks` memory — first uploads start flowing
4. Run M7 backfill for existing ONE PPAs in the bucket

Rollback: remove the `aitana_blocks` memory from `memory.tfvars` (multivac chunker stops publishing; subscriber keeps reading remaining queued messages then idles). Aitana `import-by-reference` falls through to L4 sentinel + L3 fresh parse — identical to 4.5.1 behaviour.

## Testing Strategy

**Multivac chunker:**
- `chunker/tests/test_dispatch_aitana_blocks.py` — 3 pytest: payload shape matches schema, topic = `chunker-to-aitana-blocks`, oversized doc stages to GCS pointer
- Existing chunker fan-out tests stay green (additive branch, doesn't touch DE/embed paths)

**Aitana backend:**
- `backend/tests/api_tests/test_parsed_blocks_ingest.py` — 4 pytest: valid push writes Firestore record, bad OIDC returns 403, oversized payload (GCS pointer mode) reads from staged GCS, duplicate ingest is idempotent
- `backend/tests/api_tests/test_import_by_reference.py` — add 2 pytest: L5 shared hit writes `blocksRef` (no clone), `get_blocks(doc_with_ref)` resolves to shared blocks
- `backend/tests/unit/test_get_blocks_helper.py` — pure function, 2 cases (legacy inlined, new ref)

**E2E:**
- Upload a fresh PDF to a multivac-monitored bucket → wait ~30s → verify `parsed_blocks/{hash}` exists in Firestore + Aitana `import-by-reference` for that gs:// returns the blocks via L5 hit
- Run `aiplatform docs batch-parse --dry-run` against ONE bucket → assert file count matches `gsutil ls`
- Run live backfill → assert 247 records appear within 30 min

**Multivac infra:** terraform plan + apply against dev; smoke that the new topic exists + IAM grants resolve.

## Success Criteria

- [ ] Multivac chunker publishes to `chunker-to-aitana-blocks` when any memory declares `vectorstore: aitana_blocks`
- [ ] Aitana subscriber writes to `parsed_blocks/{sha256(gs_url)}` with the full Block ADT preserved
- [ ] `import-by-reference` L5 lookup hits before falling through to L4 sentinel / L3 fresh parse
- [ ] New per-user `parsed_documents` records use `blocksRef` (no inlined blocks); legacy records still readable
- [ ] One-shot backfill of `gs://multivac-acme-energy-bucket/PPAs/` populates 247 records in ≤30 min
- [ ] First-click latency for any chunker-ingested doc ≤500ms P95 (Firestore lookup, no parse)
- [ ] Zero duplicate AILANG Parse charges between multivac aisearch ingestion + Aitana on-demand parse for any doc in the shared `parsed_blocks` set
- [ ] All multivac chunker tests stay green; new Aitana tests pass (4 + 2 + 2 = 8 new pytest); 1 new CLI test
- [ ] BigQuery `parsed_blocks_archive` table receives one row per ingested doc (audit trail mirroring `qna-to-pubsub-bq-archive` pattern)
- [ ] Observability dashboard shows: messages received, Firestore write latency P50/P95/P99, per-VAC ingest rate, payload size distribution

## Security Considerations

- **Cross-project PubSub IAM:** multivac chunker SA gets `roles/pubsub.publisher` on `chunker-to-aitana-blocks` in the Aitana project. Aitana subscriber SA gets `roles/pubsub.subscriber`. Both grants are explicit in terraform — no console grants, no broad project-level roles.
- **OIDC auth on push endpoint:** PubSub push mints an OIDC token for the configured SA; the endpoint verifies issuer + email allowlist (mirrors the `/api/admin/seed-platform-skills` pattern from 4.5).
- **No raw bucket access in subscriber:** the subscriber writes Firestore records from the message body alone — it never reads GCS directly. Bucket-read permission stays with the multivac chunker SA.
- **Data egress:** none. Multivac and Aitana are both inside the Sunholo trust boundary per [product-axioms.md §9](../../../docs/product-axioms.md#9-secure-by-construction). PubSub messages stay inside GCP. No third-party SaaS sees content.
- **Payload size guard:** `_stage_to_gcs_and_pointer` writes oversized payloads (>9 MiB) to a staging bucket and publishes a `{gs_pointer: "gs://staging/..."}` envelope instead of inlining. Subscriber resolves pointers. Prevents PubSub dropping messages silently at the 10 MiB limit.
- **Idempotency:** subscriber writes to Firestore by deterministic `blocks_id = sha256(sourceUrl)`. Duplicate messages overwrite the same doc with the same content — no double-insert risk. The chunker also retries on Aitana 5xx — the writer must be idempotent.
- **Tenant isolation:** the design assumes a single Aitana customer per deployment (Sunholo's per-deployment branding model). If multi-tenant ever returns, `parsed_blocks` would need a `tenantId` prefix on the key to prevent cross-tenant block reads.

## Related Documents

- [document-import-by-reference.md](../v6.4.0/document-import-by-reference.md) — v6.4.0 4.5.1; the L1/L2/L3/L4 cache layers this doc extends with L5
- [skill-onboarding.md](../v6.4.0/skill-onboarding.md) — v6.4.0 4.5; defines the `welcome.bucket_browser` whitelist driving which buckets become candidates for backfill
- [document-to-ai-pipeline.md](../v6.1.0/implemented/document-to-ai-pipeline.md) — v6.1.0; Block ADT JSON schema; original PubSub-based parse pipeline
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance convention
- multivac-system-services `chunker/dispatch.py` — the existing fan-out point this doc extends
- multivac-aitana `infrastructure/environments/common/terraform.tfvars` lines 187-241 — existing PubSub topic + subscription patterns

## Open Questions

1. **Subscriber as Cloud Run Job vs. main backend HTTP route?** v1 proposes a dedicated PubSub push endpoint on the main backend (zero new infra, isolated by route prefix `/api/internal/`). Alternative: separate Cloud Run service `aitana-parsed-blocks-writer` for blast-radius isolation. Decision: ship inline on main backend; split if write volume justifies a dedicated service (likely never for typical VAC throughput).
2. **Memory config opt-in vs. global flag?** Today's design uses per-memory `vectorstore: aitana_blocks` — VAC owner opts in. Alternative: global flag in `vacConfig` (`aitana_blocks_enabled: true`) that auto-publishes to the topic for all parses. Multivac convention favours per-memory; v1 follows that.
3. **`parsed_blocks` retention?** Should records expire (e.g. delete if no `parsed_documents` references it for 90 days)? v1: no retention (storage is cheap, parse cost is high). Add TTL only if Firestore costs become measurable.
4. **Should the chunker write the per-user record too?** Could pre-populate a `parsed_documents` row for every known user when a new bucket arrival appears. Decided no — the user-side record is lazy-created on first click. The L5 hit makes that creation a single Firestore write, not a parse.
5. **Tenant-scoped buckets that span multiple Aitana deployments?** Today each deployment has its own Firestore. If a shared bucket lives in multivac with multiple Aitana consumers (e.g. internal demo deployment + ONE prod), each Aitana writes its own `parsed_blocks`. Fan-out from the chunker via multi-subscription is the right answer — same topic, multiple subscriptions. Out of scope for v1.

## Implementation Report

_(To be filled after sprint completes — same format as 4.5/4.5.1's implementation reports when this doc is moved to `implemented/`.)_
