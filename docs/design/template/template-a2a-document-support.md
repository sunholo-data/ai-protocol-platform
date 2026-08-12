# Template A2A Document Support — Files Inbound + Org Bucket Outbound (G46)

**Status**: Implemented (Sprint A2A-DOCS, 2026-06-08)
**Priority**: P0 (production failures observed 2026-06-07/08; every fork serving file-aware skills hits these patterns)
**Estimated**: 1-2d planned; ~3h actual (port from gde-ap-agent landed cleanly — Friction 29/30 already documented + tested)
**Scope**: Backend (a2a_invocation.py + new file_extraction.py + new org_documents.py + 2 tools) + Cloud Build + IAM + Tests
**Dependencies**: G45 (A2A invocation bridge) — this doc adds capabilities on top of the `/a2a` mount
**Created**: 2026-06-08
**Last Updated**: 2026-06-08
**Source items**: gde-ap-agent fork production debugging, 2026-06-07 → 2026-06-08 (commits `aaf0315` M1 file extraction, `10f7a93` M2 org-scoped bucket, `7687784` M3 force_new_version fix). Upstream brief §6 (sections 6a–6f) + Friction 29 (force_new_version=True trap) + Friction 30 (Discovery Engine rejects custom metadata) in `<local-path>` lines 1178–1444.

## Problem

G45 (shipped 2026-06-08) gave peer agents and Gemini Enterprise an A2A `message/send` endpoint that actually executes through ADK. Within hours of going live, two file-handling failure modes hit the gde-ap-agent fork in production:

**Scenario A — peer sends a fresh file.** Real failure 2026-06-07T22:06:32 UTC: Gemini Enterprise routed a user upload to `/a2a` with ~2KB payload instead of the ~37KB the actual `.docx` should have been. GE silently stripped the file at the peer side because the agent card's `defaultInputModes` advertised only `["text"]` — peers don't bother uploading bytes they think the agent won't accept. Agent saw `document_ids=[]`.

**Scenario B — peer asks about existing documents.** The agent has standing access to a curated GCS corpus (vendor master, historical invoices, contracts). A peer wants to query it conversationally without uploading anything new. No convention exists for "this deploy is bound to this bucket." Hardcoding paths in skill instructions or per-fork env vars doesn't scale across organisations.

A third silent failure showed up at 2026-06-08T04:45 UTC when the team tried to add an interceptor to handle Scenario A: ADK's `A2aAgentExecutor` has two code paths (NEW with interceptors, LEGACY without), and it picks NEW only when `force_new_version=True` is passed at construction OR the peer sends a "new-version" hint via `X-A2A-Extensions`. **Gemini Enterprise does not send that hint.** Without `force_new_version=True`, the interceptor surface is silently inert against the most common peer. Code looks correct; tests pass; production drops every file.

## Goals

End-state: peer agents and Gemini Enterprise can both push files INTO and query existing documents OUT of the platform via A2A, with no skill-code changes required (the existing `make_document_loader` `before_agent_callback` runs on its current `document_ids` contract).

**Success metric:** Live POST to `/a2a` with a `FilePart` (PDF or `.docx`) returns `HTTP 200` with the file persisted as a `doc:{id}.json` artifact AND the agent answering with grounded reference to the document. Org bucket: tool calls `list_org_documents` returns the curated GCS objects, `read_org_document(name)` loads the document into session state, agent answers from it.

## Axiom Alignment (+9)

| Axiom | Score | Reasoning |
|---|---|---|
| INSTANT FEEL | 0 | Orthogonal — file handling is correctness, not latency |
| EARNED TRUST | +2 | "Agent silently sees empty document_ids" is the worst possible trust failure — close it BEFORE more forks hit it |
| SKILLS NOT FEATURES | +1 | File-aware skills now work over A2A without per-skill plumbing — the artifact contract stays the same |
| RIGHT MODEL RIGHT MOMENT | 0 | Orthogonal |
| GRACEFUL DEGRADATION | +2 | Tools return `[]` / `ok=False` when no bucket bound; interceptor no-ops when disabled; single-tenant deploys that don't use the feature pay zero cost |
| PROTOCOL OVER CUSTOM | +2 | A2A `FilePart` is the spec; ADK's `A2aAgentExecutorConfig.execute_interceptors` is the canonical extension point. The Friction 30 anti-pattern (custom `metadata` field) is explicitly rejected in favour of standard env-var config |
| API FIRST | +1 | Two new tools (`list_org_documents`, `read_org_document`) become invocable from the agent loop, no UI gymnastics |
| OBSERVABLE | +1 | Interceptor logs `logger.info` on every FilePart processed; clear "interceptor ran but no FileParts" vs "interceptor didn't run at all" disambiguation |
| SECURE BY CONSTRUCTION | +1 | SA scoped to `roles/storage.objectViewer` on the specific bucket — no wildcard grants; MIME + size + URI scheme validated before persist |
| THIN CLIENT FAT PROTOCOL | 0 | Orthogonal |
| **Net** | **+9** | Strong signal — fork-blocking real-world failure, well-tested upstream impl |

No axiom scores -1. Hard-fail rules (no -1 on EARNED TRUST for user-facing data; no -1 on SECURE BY CONSTRUCTION for new data access) both pass.

## Standards Compliance

| Concern | Standard / source | Decision |
|---|---|---|
| File transport over A2A | A2A v0.2 `FilePart` (sibling of `TextPart`); `defaultInputModes` on card advertises supported MIME types | Adopt as-is. `inputModes` already lists MIME types in the spec; we just extend the array |
| Interceptor hook | ADK's `A2aAgentExecutorConfig.execute_interceptors` | Adopt. Documented in ADK source; takes `Callable[[RequestContext], Awaitable[RequestContext]]` |
| New-vs-legacy executor path | ADK source `_use_legacy` / `_force_new_version` branch | Pin `force_new_version=True`. NOT a standard — ADK-specific. Locks us into the supported path |
| Per-deploy bucket binding | Env var `A2A_AGENT_DOCUMENTS_BUCKET=gs://...` matching the `A2A_AGENT_NAME`/`_DESCRIPTION`/`_ICON_URL` convention from G43/§5 | Adopt the existing convention; no new pattern |
| Per-registration metadata | Discovery Engine Agent resource schema | **Rejected** — Friction 30 confirms `metadata` field doesn't exist on the resource. Use env var instead |

No custom protocols invented. Friction 30 explicitly documents the standards-check we did and the path Google's API does not currently support.

## Design

### Two layers, both env-gated

```
PEER AGENT (Gemini Enterprise)
        │
        │  POST /a2a/  (message/send with FilePart inside message.parts)
        ▼
NEXT.JS INGRESS (G45 rewrites — unchanged)
        ▼
FASTAPI SIDECAR → STARLETTE SUB-APP (G45 mount — unchanged)
        ▼
A2aAgentExecutor (G45 — gets force_new_version=True flag added)
        │
        ├─→ FileExtractionInterceptor (NEW, env-gated by ENABLE_A2A_FILE_INPUT)
        │     1. Walk message.parts for FileParts
        │     2. Validate MIME (defaultInputModes ∩ A2A_AGENT_INPUT_MIME_TYPES)
        │     3. Validate size (A2A_FILE_MAX_BYTES default 25 MB)
        │     4. Persist each as doc:{id}.json via artifact_service
        │     5. STRIP FileParts from message.parts (prevents double-injection)
        │     6. Append doc_ids to session_state["document_ids"]
        ▼
ADK Runner → root_agent
        │
        ├─→ Standard make_document_loader before_agent_callback (UNCHANGED)
        │     Sees state["document_ids"] populated, loads docs into context
        │
        └─→ root_agent tools (extended with TWO new tools, env-gated by A2A_AGENT_DOCUMENTS_BUCKET)
              ├─ list_org_documents(prefix="")  →  GCS object names visible to the SA
              └─ read_org_document(name)        →  persists as doc:{id}.json + appends to document_ids
```

### Why `force_new_version=True` (Friction 29)

The single most expensive thing for a fork to discover the hard way. ADK's executor branches:

```python
should_use_new_impl = not self._use_legacy and (
    self._force_new_version or self._check_new_version_extension(context)
)
```

`_check_new_version_extension` looks for a hint in the peer's `X-A2A-Extensions` header. **Gemini Enterprise never sends that hint** (verified 2026-06-08T04:45 UTC against live deploy). Without `force_new_version=True`, the legacy path runs, interceptors are bypassed, and any FilePart goes straight to Vertex's strict MIME validator which throws `400 INVALID_ARGUMENT`. The error message blames "the file" but the fix is one line on the executor construction.

`build_a2a_app()` defaults this to `True` in the template — the legacy path exists for backwards compatibility with a small set of peers that DO send the hint; new feature work should always be on the new path.

### Why interceptor + strip, not skill-side parsing

A FilePart left in `message.parts` triggers ADK's `convert_a2a_part_to_genai_part` which inlines the file content into the Gemini turn. Combined with our `doc:{id}.json` artifact persist + the existing `make_document_loader` injection, the file would be DOUBLE-injected — once raw bytes via Gemini, once parsed-text via the loader. Strip + persist is the only correct path.

The interceptor matches what `convert_a2a_part_to_genai_part` would have done, but routes the result through the loader instead of Gemini's MIME validator (which doesn't accept all the formats we parse).

### Why env-gated, not always-on

Forks that don't serve file-aware skills shouldn't pay for filesystem writes per A2A call. Default `ENABLE_A2A_FILE_INPUT=false`; opt in by setting the env var. The interceptor short-circuits to a no-op return when disabled, so the cost is one `os.environ.get()` per request.

### Why org bucket is single-tenant (Scenario B + Friction 30)

The cleanest tenant-scoping for org documents would be per-registration metadata on the Discovery Engine Agent resource — "this registration is bound to bucket X". Friction 30 documents the standards check: the Agent resource schema has no `metadata` field. PATCHing one returns `INVALID_ARGUMENT: Unknown name "metadata" at 'agent'`.

For now: one env var per deploy, `A2A_AGENT_DOCUMENTS_BUCKET=gs://...`. All peers reaching this deploy share the same bucket. Multi-tenant forks needing per-call bucket lookup must wait for Google to add a peer-identification header (none observed yet on GE traffic) and implement a Firestore-keyed lookup themselves. The design ships the documented fork extension point; doesn't implement the multi-tenant path.

## CLI Surface

Two new commands fit the v6 CLI convention. Both small wins for operators:

```bash
# Verify the bound bucket is reachable from the running SA
aiplatform a2a probe-org-bucket
# → checks A2A_AGENT_DOCUMENTS_BUCKET env on deployed service,
#   issues a list call with SA auth, reports object count + first 5 names

# Test the inbound file path locally (without GE)
aiplatform a2a send-file <local-path> --skill <skill-id>
# → POSTs a message/send with the file as FilePart against the local /a2a mount,
#   prints the resulting doc_ids and the agent's reply
```

Implementation: 2 small Click subcommands under `cli/aitana/a2a.py`, each ~30 LOC. Reuses `aiplatform`'s existing httpx + auth scaffolding.

## Implementation Plan

| Item | File | LOC | Source |
|---|---|---|---|
| 1. Extend `defaultInputModes` on card | `backend/protocols/a2a.py` (edit `_build_card`) | 10 | §6a |
| 2. New env var `A2A_AGENT_INPUT_MIME_TYPES` parsed in card builder | `backend/protocols/a2a.py` | 5 | §6a |
| 3. `FileExtractionInterceptor` module | `backend/protocols/file_extraction.py` (NEW) | 180 | §6b |
| 4. `force_new_version=True` + `execute_interceptors=[FileExtractionInterceptor]` wiring | `backend/protocols/a2a_invocation.py` (edit `build_a2a_app`) | 15 | §6c (Friction 29) |
| 5. Org bucket helpers + 2 FunctionTools | `backend/tools/org_documents.py` (NEW) | 200 | §6d |
| 6. Add tools to `root_agent` when env var set | `backend/app.py` (edit) | 8 | §6d |
| 7. Cloud Build env vars | `cloudbuild.yaml` | 4 | §6e |
| 8. SA grant `roles/storage.objectViewer` on bound bucket | Manual / Terraform | 0 | §6e |
| 9. 2 CLI commands | `cli/aitana/a2a.py` (NEW) | 60 | New |
| 10. 8 unit tests | `backend/tests/api_tests/test_file_extraction.py` + `test_org_documents.py` (NEW) | 250 | Port |
| 11. Extend `scripts/simulate-a2a-peer.py` with file-send step | `scripts/simulate-a2a-peer.py` | 40 | New |
| 12. Update `scripts/verify-a2a.sh` to check `defaultInputModes` includes file MIMEs when `ENABLE_A2A_FILE_INPUT` is advertised | `scripts/verify-a2a.sh` | 20 | New |
| 13. Update `gemini-enterprise.md` Troubleshooting with file-input failure modes | `docs/integrations/gemini-enterprise.md` | 30 | §6 |

Total: ~800 LOC code + ~250 LOC tests + ~100 LOC scripts/docs across ~10 files.

### Sprint shape (matches A2A-INVOKE 2-day pattern)

**Day 1 (4-5h):**
- M1 — `FileExtractionInterceptor` + interceptor wiring + `force_new_version=True` + 4 tests (TDD-first, regression guard for Friction 29)
- M2 — Card `defaultInputModes` extension + env var override + 2 tests
- Local smoke: `make dev`, POST a `.docx` to `/a2a` directly, confirm `doc:{id}.json` artifact created + session state has `document_ids`

**Day 2 (3-4h):**
- M3 — `list_org_documents` + `read_org_document` tools + `root_agent` wiring + 4 tests
- M4 — Cloud Build env vars + SA grant + CLI commands + Sunholo deploy + GE end-to-end smoke with real file
- M5 — Docs: update gemini-enterprise.md troubleshooting; SEQUENCE.md G46 entry; mark sprint Implemented

## Testing

### Critical regression guards

1. **Friction 29 guard** — assert `A2aAgentExecutor` is constructed with `force_new_version=True`. If a future refactor removes the flag, this test fails BEFORE the interceptor silently goes inert in production. ~5 LOC.

2. **Double-injection guard** — POST a message with a FilePart, assert the interceptor's "stripped" message.parts has length N-1, AND assert no FilePart is in the genai-side conversion output. Catches the "we forgot to strip" footgun.

3. **Disabled-mode no-op guard** — with `ENABLE_A2A_FILE_INPUT` unset, POST same payload, assert the interceptor returns context unchanged AND no artifact write occurs.

### Local

```bash
make dev    # backend + frontend

# Direct backend (skip GE, just exercise the interceptor):
curl -X POST http://localhost:1956/a2a/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"f1","method":"message/send","params":{"message":{"role":"user","messageId":"f1","parts":[{"kind":"text","text":"Analyze this"},{"kind":"file","file":{"name":"invoice.docx","mimeType":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","bytes":"<base64>"}}]}}}'
# → expect: HTTP 200, response.result.id present, doc:{id}.json artifact in session state

# Org bucket tools:
aiplatform a2a probe-org-bucket
# → expect: lists objects in A2A_AGENT_DOCUMENTS_BUCKET if SA has access
```

### Post-deploy (Gemini Enterprise end-to-end)

The real interop test that takes the longest to set up but catches the most:

1. Upload a `.docx` through the Gemini Enterprise workspace UI targeting our registered agent
2. Watch backend logs for `file_extraction.py: persisted doc:abc123 from FilePart` 
3. Agent's response references the document's content (not "I see no attachments")
4. Org bucket: ask "what historical invoices do you know about?" → agent calls `list_org_documents` → reads the most relevant → answers grounded in its content

If step 2 doesn't log but the deploy is correct, the interceptor isn't running → confirm `force_new_version=True` is on the deployed revision (Friction 29).

## Migration / Fork Impact

**Existing G45 forks** — no breaking change. `ENABLE_A2A_FILE_INPUT` and `A2A_AGENT_DOCUMENTS_BUCKET` both default to unset/disabled. Card emits the extended `defaultInputModes` regardless (peers ignore MIME types they don't support; no harm), but the interceptor stays inert and the org tools aren't registered. Forks opt in by setting either env var.

**Sunholo deploy** — set both env vars in `cloudbuild.yaml`; bind to an existing org bucket. The deployed agent gains file-input from GE + org-document query from any peer. No agent re-registration needed (the card auto-updates; GE re-fetches on its own discovery cadence — `iconUrl` precedent confirms this works).

**Workshop attendees (July 2026)** — every fresh `git clone` of the public template now ships full A2A document support. The "drop a file in GE → agent reads it" demo works without any per-attendee plumbing.

## Related Documents

- [template-a2a-invocation-bridge.md](template-a2a-invocation-bridge.md) — G45, the invocation surface this doc extends
- [template-a2a-spec-compliance.md](template-a2a-spec-compliance.md) — G43, the discovery layer (this doc extends `inputModes` advertised there)
- [docs/integrations/gemini-enterprise.md](../../integrations/gemini-enterprise.md) — operator's guide; gets new Troubleshooting entries for file-input failures
- [docs/design/v6.1.0/local-dev-cli.md](../v6.1.0/local-dev-cli.md) — the CLI affordance lives here
- Upstream brief: `<local-path>` §6 (1178-1356) + Frictions 29 (1358-1397) + 30 (1401-1443)
- Source commits to port from: gde-ap-agent `aaf0315`, `10f7a93`, `7687784`

## Open Questions

1. **Which root_agent gets the org tools?** The template's `root_agent` from `backend/app.py` (`name="aitana"`) is generic. If a fork's root agent is specialised (gde-ap-agent has `ap-orchestrator`), they'll want to attach the tools to that specific agent, not the template root. Solution: template wires tools to `root_agent`; forks override by editing their own `app.py`. Document.

2. **Should `read_org_document` also work for FILES uploaded inline?** No — those go through the interceptor path. Org tools are explicitly for the pre-existing-bucket case. Documenting the boundary in the orchestrator instruction.

3. **Where do CLI commands live in the existing tree?** Probably `cli/aitana/a2a.py` (new module) alongside the existing `skill.py`/`session.py` Click groups. Confirm during sprint planning.

4. **What happens if `A2A_AGENT_DOCUMENTS_BUCKET` is set but the SA lacks objectViewer?** The tools should fail gracefully — log warning, return `[]` from `list_org_documents`, return `{"ok": False}` from `read_org_document`. NOT 500 the agent turn. Important for "I set the env but forgot the IAM grant" debugging.

5. **Multi-tenant per-call bucket lookup** — explicitly out of scope per Friction 30. Document the Firestore-lookup pattern as a fork extension; don't implement.

## Sprint Plan + JSON

Following the sprint-planner convention from A2A-INVOKE: spawn `sprint-planner` skill with this doc as input once approved. Target sprint ID: `A2A-DOCS` or `A2A-FILE-IO`. G-number: G46.
