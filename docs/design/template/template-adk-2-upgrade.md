# ADK 2 upgrade — project hub

**Status**: In progress. 03a and the compaction port landed; the coordinated 02/03b OpenTelemetry security upgrade is prepared for review. Deployed Phase 0/1 gates remain pending.
**Priority**: P1
**Estimated**: ~3–4 weeks in total. Phase 0+1 take ~2–2.5 weeks and are sequential. Phase 2 takes ~1–1.5 weeks and its sub-docs can run in parallel.
**Scope**: Backend and frontend dependencies, ADK seams we customise, CI gates, plus a set of adopt-a-feature follow-ups
**Created**: 2026-09-30
**Last Updated**: 2026-10-16. Turned into a project with linked sub-docs; targets refreshed to ADK 2.12, ag-ui-adk 0.9, mcp 2.3 and a2a-sdk 1.2; added the patterns from `google/adk-recipes`.
**Source items**: Sunholo deployment research, 2026-09-30. Triggered by the MCP concurrent-close hang (PR #20) and the cold-import stall (PR #21). Refreshed 2026-10-16 against the ADK changelog (1.32.0 → 2.12.0), the ag-ui-adk changelog (0.7.0 → 0.9.0) and the [`google/adk-recipes`](https://github.com/google/adk-recipes) "long-horizon-harness" (Horizon) and `staged-ui-widgets` recipes.

This document is the hub. It holds the goal, the version targets, the phase order, the sub-doc index and the shared exit criteria. Each sub-doc owns its own findings, plan and tests. The 2026-09-30 research is now spread across them.

## Why

We pin four fast-moving dependencies well behind head:

| Package | Pinned | Latest (2026-10-16) | Jump |
|---|---|---|---|
| `google-adk` (+ `[eval]`) | 1.31.1, exact pin | **2.12.0** (2026-10-13) | A major + 12 minors. A 1.x maintenance line continues (1.39.1). |
| `ag-ui-adk` | 0.6.0 (`>=0.6.0,<1.0.0`) | **0.9.0** (2026-10-10) | 3 minors, each with breaking changes. 0.8 requires `ag-ui-protocol>=1.0`. |
| `@ag-ui/client` / `@ag-ui/core` (frontend) | ^0.0.52 | **1.0.3** | Protocol 1.0: `RUN_STARTED.protocolVersion`, `resume[]` |
| `mcp` (Python SDK) | 1.27 in the lock (`>=1.7.1` in pyproject) | **2.3.0** | A major (a rewrite). Optional: ADK ≥2.9 supports both. |
| `a2a-sdk` | `<1.0.0` | **1.2.2** | A major. ADK ≥2.6 allows 1.x; Horizon requires it. |
| `google-genai` | `>=1.19` (1.73 in the lock) | 2.31.0 | ADK 2.10+ forces ≥2.19 |

Three reasons to move:

1. **Bugs live in this code.** The bugs fixed in PR #20/#21 were in toolset close, MCP teardown and lazy `vertexai` imports. Upstream has fixed many neighbouring bugs since 1.31, including an OAuth-secret leak through `/run`, `/run_sse` and the session endpoints (ADK changelog line 167).
2. **The features we want are 2.x-only.** These include `FallbackModel`, plugins (`ReflectAndRetryModelPlugin`, `ToolCallIntegrityPlugin`, Model Armor), the `SkillToolset` activation APIs, `ContextCacheConfig` with Anthropic breakpoints, Memory Bank `allowed_topics`, A2A task mode, and rubric and efficiency eval metrics. Every `adk-recipes` reference pattern also requires `google-adk>=2.8`.
3. **The cost of waiting compounds.** Each minor adds behaviour changes at the seams we customise: callbacks, transfer, finality and compaction. Mark (2026-09-30): *"AI is a fast moving environment — we will need to be sure about regression tests."*

## Phases and sub-docs

```
Phase 0  GATE ───────────► Phase 1  BUMP (sequential, one PR each) ──────────► Phase 2  ADOPT (parallel)
 [01] regression gate        [02] AG-UI stack (ag-ui-adk, protocol 1.0)        [07] native replacements
                             [03] ADK 1.31 → 1.39.1 → 2.12 core bump           [08] run guardrails
                             [04]   └ compaction port (blocks 03's 2.x step)   [09] staged A2UI emission
                             [05] a2a-sdk 1.x                                  [10] prompt prefix + context caching
                             [06] mcp 2.x                                      [11] memory loop
```

| # | Sub-doc | Phase | Est | Blocks / blocked by | One line |
|---|---|---|---|---|---|
| 01 | [template-adk-2-regression-gate.md](template-adk-2-regression-gate.md) | 0 | 2–3d | blocks everything | Pin current behaviour before touching a version: conformance tests for the new seams, a session round-trip across versions, an eval baseline and a headless journey pass rate. |
| 02 | [template-adk-2-agui-stack.md](template-adk-2-agui-stack.md) | 1 | 2–3d | after 01. Independent of 03: ag-ui-adk 0.9 requires `google-adk>=1.28.1,<3` | ag-ui-adk 0.6 → 0.9, ag-ui-protocol 1.0, `@ag-ui/client` 1.0. `protocolVersion`, session-lookup errors, `resume[]`. |
| 03 | [template-adk-2-core-bump.md](template-adk-2-core-bump.md) | 1 | 4–6d | after 01; its 2.x step needs 04 | ADK 1.31.1 → 1.39.1 → 2.12 with extras, forced dependencies, and every behaviour change at our seams (transfer, callbacks, finality, sessions, telemetry). |
| 04 | [template-adk-2-compaction-port.md](template-adk-2-compaction-port.md) | 1 | 1–2d | blocks 03's 2.x step | Two private compaction helpers are gone in 2.x. Port the pending-call truncation and own it in-tree with a parity test. |
| 05 | [template-adk-2-a2a-sdk-1.md](template-adk-2-a2a-sdk-1.md) | 1 | 1–2d | after 03 | a2a-sdk 0.3 → 1.x across `protocols/a2a*`, re-verified against Gemini Enterprise registration and invocation. |
| 06 | [template-adk-2-mcp-2.md](template-adk-2-mcp-2.md) | 1 | 2–3d | after 03 (optional, last) | mcp 1.x → 2.x: FastMCP → MCPServer, waiter wake-up fix, re-test every remote server. Keep the `TaggedMcpToolset` lease. |
| 07 | [template-adk-2-native-replacements.md](template-adk-2-native-replacements.md) | 2 | 3–5d | after 03 | Evaluate replacing homegrown code with ADK natives, one at a time behind a conformance test: `FallbackModel`, `ToolConfirmation`/`request_input`, `SkillToolset`, media results, native metrics, `to_mcp_server`. |
| 08 | [template-adk-2-run-guardrails.md](template-adk-2-run-guardrails.md) | 2 | 1–2d | after 03 | Iteration-budget, no-progress and repeated-failure halts as one plugin with a single `halt_reason` that always reaches the UI (Horizon pattern). |
| 09 | [template-adk-2-staged-a2ui.md](template-adk-2-staged-a2ui.md) | 2 | 2–3d | after 03 | Tools stage UI payloads in state; one end-of-turn emitter renders them. A structural fix for "A2UI won't render" (`staged-ui-widgets` pattern). |
| 10 | [template-adk-2-prompt-caching.md](template-adk-2-prompt-caching.md) | 2 | 2–3d | after 03 | A byte-stable `static_instruction` prefix + `ContextCacheConfig` (incl. Anthropic breakpoints). Cost and first-token latency (TTFT). |
| 11 | [template-adk-2-memory-loop.md](template-adk-2-memory-loop.md) | 2 | 2–3d | after 03 | Memory Bank `allowed_topics`, a pre-compaction memory flush, and a scheduled consolidation pass on top of `memory_autosave`. |

Order within Phase 1 (each step is a separate port-up PR and merge-down):

1. **02**: ag-ui-adk 0.9 + ag-ui-protocol 1.0 + `@ag-ui/client` 1.0, as one PR because the backend and frontend must move together. It runs on ADK 1.31.1: 0.9.0 requires `google-adk>=1.28.1,<3` (checked against the PyPI metadata, 2026-10-16). Doing it first isolates the wire-protocol change from the runtime change.
2. **03a**: ADK 1.39.1, the last 1.x. Low risk. MCP fail-fast arrives, the compaction helpers still exist, and genai 2 / starlette 1 conflicts surface early.
3. **04**: compaction port. It must land before 03b.
4. **03b**: ADK 2.12 with extras, still on mcp 1.x and a2a-sdk 0.3.
5. **05**: a2a-sdk 1.x.
6. **06**: mcp 2.x (optional; can trail Phase 2).

## Shared exit criteria (every Phase 1 PR)

A step is done when **all** of these pass on that PR. Each sub-doc may add its own.

- `make adk-conformance` is green, including the new contract tests from [01](template-adk-2-regression-gate.md).
- `cd backend && make lint && make test-fast` and `cd frontend && npm run quality:check` are green.
- `make template-parity` and `make publish-check` are green.
- **Headless journeys on dev hit the 01 baseline pass rate (10/10 per journey).** These are runs of `aiplatform skill chat`: greeter handoff, confirm-handoff, an MCP skill, a document fetch (artifact save→load), a long turn that triggers compaction, browser, and an A2UI workspace render on **both** the chat path and the `surface-action-run` path.
- **Session round-trip:** a session written on the previous version is read and continued on the new one, on dev.
- `adk eval` before/after shows no rubric regression. Duration, token and call-count deltas are recorded in the sub-doc.
- **Rollback is written down:** the previous image tag, and whether sessions written by the new version are readable by the old one. Until proven otherwise, 2.x session writes are treated as **one-way per environment**.

## Version-skew rules

- `google-adk` and `google-adk[eval]` stay pinned **exactly and in lockstep** (as since v6.17.0). No ranges.
- ag-ui-adk is bumped as a separate step from ADK. 0.6.0 caps `google-adk<2`, so 02 must land before 03b.
- The frontend `@ag-ui/*` and backend `ag-ui-protocol` move together (02), never apart. `RUN_STARTED` must stay the first event on the wire (see `backend/adk/CLAUDE.md`).
- mcp stays on 1.x until 06; ADK 2.9+ resolves 1.x by default.

## Out of scope

- Agent Platform managed **Sandboxes** and per-user BYOC sandboxes (Horizon). We have `mcp-sandbox` and the code executor, so this is a separate design if wanted.
- **Graph workflows / YAML workflows** (2.0 GA, 2.9). Worth a design of its own once on 2.x; no current pain point demands one.
- **Live / voice** (`google.adk.live`, `CascadeLive`, the LiveKit runner). Only the regression check that live turns now run model callbacks is in scope (03).
- Moving off Cloud Run to Agent Runtime / Agent Engine hosting.

## Reference material

- ADK 2.0 migration guide: <https://adk.dev/2.0/>
- ADK changelog: <https://github.com/google/adk-python/blob/main/CHANGELOG.md>. Line numbers cited in sub-docs refer to the 2026-10-16 copy, where 2.12.0 starts at line 3 and 1.31.0 at line 2147.
- ag-ui-adk changelog: <https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/adk-middleware/python/CHANGELOG.md>
- MCP Python SDK 2.x migration: <https://py.sdk.modelcontextprotocol.io/migration/>
- `google/adk-recipes` (successor to `adk-samples`): <https://github.com/google/adk-recipes>
  - `core/python/long-horizon-harness` ("Horizon"): `AGENTS.md` maps each pattern to the function that implements it. Requires `google-adk[mcp,otel-gcp,a2a]>=2.8,<3`, `a2a-sdk>=1.0`. It assumes one trusted user per deployment, so its artifact-link and sandbox handling must **not** be copied as-is (CLAUDE.md security rule).
  - `contrib/python/staged-ui-widgets`: the staged A2UI emission pattern used by sub-doc 09.
- Our ADK contracts: [`docs/design/v6.17.0/adk-contract-checklist.md`](../v6.17.0/adk-contract-checklist.md) (C1–C7) and the decision record [`toolconfirmation-decision.md`](../v6.17.0/toolconfirmation-decision.md).

## Open questions

- Can ADK 1.x read sessions written by 2.x on Agent Engine (rollback safety)? It is undocumented, and 01 tests it on dev. 2.0 adds `node_info`/`output` to the event schema, and 2.12 moves the Vertex client to `google-cloud-aiplatform` v2.2.
- Upstream fix for `Runner.close()` closing shared toolsets: [adk-python PR #7045](https://github.com/google/adk-python/pull/7045) (#4045) was still open at 2.10. Re-check at 2.12 before 06.


## 2026-10-23 security PR status

The OTel 1.45.1 parser fix requires ADK 2 and a compatible transport, so 02 and
03b are combined for this review, including frontend client/core 1.0.3.
[03b records the compatibility decisions and pending deployed checks](template-adk-2-core-bump.md#03b-review-evidence-2026-10-23).
This does not complete the shared exit criteria or authorize a production
promotion. Rollback across ADK 1.x/2.x remains unproven for the target environment;
treat new session writes as one-way until the reverse round-trip is verified.
