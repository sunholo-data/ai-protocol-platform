# ADK 2 upgrade · 03 — Core bump: ADK 1.31.1 → 1.39.1 → 2.12

**Status**: 03a landed; 03b implemented for review, deployed exit gates pending
**Phase**: 1. Two PRs: 03a (1.39.1) and 03b (2.12). 03b needs [04](template-adk-2-compaction-port.md) and [02](template-adk-2-agui-stack.md) landed first.
**Estimated**: 4–6 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-23

## 03a — ADK 1.39.1 (last 1.x)

**Low risk.** It gives us MCP fail-fast on a dead transport (1.32, on by default; [7744cfe](https://github.com/google/adk-python/commit/7744cfe0f36a74f50abb53ec0d42566c439257b3)) and `BufferableSessionService` (1.33). It still has every private compaction helper we import (verified against the 1.39.1 wheel). Its forced dependencies surface the genai 2 / starlette 1 conflicts before the major.

Bump `google-adk` and `google-adk[eval]` in lockstep. Run the shared exit criteria.

### 03a results (2026-10-17)

The pin moved to `google-adk==1.39.1` / `google-adk[eval]==1.39.1` and the lock was redone (operator commit). As a result the lock now forces google-genai 2.31, starlette 1.8 and litellm 1.85.7. Before any fix, `pytest -m "not slow and not integration"` on this branch gave **9 failed, 3913 passed**. All 9 failures had one cause.

| Behaviour change in 1.39.1 | Where it showed | Our code | Action |
|---|---|---|---|
| `LlmEventSummarizer.maybe_summarize_events` now reads `llm_response.usage_metadata` from the summariser's response and copies it onto the compaction `Event` (`google/adk/apps/llm_event_summarizer.py:113`, `usage_metadata=summary_usage_metadata`). 1.31.1 never touched it. | 8 tests in `test_compaction_never_silent.py` and `test_compaction_concurrency.py::test_two_concurrent_summarisations_both_complete`: `AttributeError: 'R' object has no attribute 'usage_metadata'`. | `adk/compaction_summarizer.py` (`FidelityEventSummarizer`) calls `super().maybe_summarize_events` and returns ADK's event unchanged. In production its LLM is a `ResilientLlm` from `resolve_model_chain`, which passes its members' `LlmResponse` objects through untouched. `LlmResponse.usage_metadata` is a declared optional field that defaults to `None`, so production responses always have it. | **Test-stub fix, no production change.** The stubs yielded a duck-typed `type("R", (), {"content": …})()` object with no `usage_metadata`. Real ADK responses never look like that. They now yield a real `LlmResponse`: the never-silent stub sets usage numbers, and the concurrency stub sets content only. No assertion changed. Added `test_summary_usage_rides_the_compaction_event`, which pins that the summariser's usage reaches the compaction event through our override. |
| Consequence of the row above: the compaction event is now the newest session event that carries `usage_metadata`. `_latest_prompt_token_count` (`apps/compaction.py`) takes the newest event's `prompt_token_count` as the token-threshold trigger. | No failing test. | Nothing of ours reads `usage_metadata` off *session events*. The only production reader is `budget/callback.py:207`, which reads the live `llm_response` in the model callback. The summariser call does not go through agent callbacks, so it is neither charged twice nor charged by the budget enforcer at all. That was already true on 1.31.1. | No change. The next turn's model events supersede the compaction event's count before the next threshold check. If a turn produces no model usage (for example an error turn), the check sees the summariser's prompt size, which is about the size of the compacted span. `_events_to_compact_for_token_threshold` then filters to events after the compaction's end, so at worst this is a no-op. Watch the compaction-triggering journey on dev. |

The private compaction helpers the tests import are still present on 1.39.1: `_events_to_compact_for_token_threshold`, `_latest_compaction_end_timestamp`, `_pending_function_call_ids` and `_truncate_events_before_pending_function_call`. 1.39.1 also adds `_truncate_events_before_hitl_signal` to both compaction paths. This changes nothing in our tests and is relevant to 04's port.

**Phase 0 contracts on 1.39.1.** All 18 `adk_contract` files pass unchanged, 56 tests in total. That includes the eight 01 contracts (transfer declaration shape, before-tool contextvars, error-event finality, hallucinated tool name, instruction template literals, after-model replacement metadata, in-memory append to an unknown session, `ResilientLlm` interrupt passthrough). So none of the 2.12 differences 01 recorded have arrived yet on the 1.x line, and they all remain 03b work.

**Verified here:** each `adk_contract` file, run in batches with `-m adk_contract`. Both changed test files pass in full (16 tests). Both pass `ruff check` and `ruff format --check`.
**Not verified here:** a single run of the whole `tests/ -m "not slow and not integration"` suite (the sandbox only runs named test files), `make lint` across the repo, the frontend, headless journeys, the cross-version session round-trip, `adk eval` before/after and the rollback note. The last four need a deployed dev environment.

## 03b — ADK 2.12

### Dependency changes

From the 2026-09-30 research, rechecked against 2.12:
- **Extras split.** `mcp`, `aiplatform[agent-engines]`, GCS and the OTel exporters moved into extras. Use `google-adk[gcp,mcp,a2a,db]==2.12.0` + `google-adk[eval]==2.12.0`. Do **not** take `[extensions]`; it drags in crewai/langgraph (see the `pyproject.toml` comment).
- `google-genai` → ≥2.19. Only the Interactions API broke, and we don't use it. `opentelemetry-instrumentation-google-genai` → ≥0.7b1 for genai 2.x (changelog line 1730).
- `starlette` → ≥1.3.1. None of the removed APIs are used.
- `litellm` → ≥1.84.0 (1647). It is used for the Claude/OpenAI models in `adk/agent.py`.
- OpenTelemetry API/SDK **1.45.1**, deliberately overriding ADK 2.12's ≤1.42.1 ceiling to fix CVE-2026-114603. ADK 1.x cannot import against this SDK because it imports the removed Events API. OTLP packages move together to 1.45.1; GCP Logging needs ≥1.12.0a0 because 1.11 imports the removed `LogData`. Real parser, startup imports and conformance tests guard this override. Remove it once ADK permits the patched stack natively.
- **2.12: the Vertex client moved to `google-cloud-aiplatform` v2.2 / `agentplatform` (33).** Vertex clients are now reused per event loop (144). Check that `ResilientVertexSessionService` and `VertexAiMemoryBankService` construction still works with our lazy `vertexai` import warm-up (PR #21).

### Behaviour changes at our seams

Each row should already have a failing or passing test from [01](template-adk-2-regression-gate.md).

| Seam (contract) | Change (changelog line) | Our code | Action |
|---|---|---|---|
| Handoff (C6) | `transfer_to_agent` gains an opt-in `transfer_reason` (609); transfers are restricted to declared targets (652, 487); `transfer_to_agent` is injected for HITL resume (1060). | `adk/agent.py` enum-constrained guard + `make_handoff_policy_callback`; `adk/a2ui_elicitation_render.py`; `adk/callbacks.py` `_INTERNAL_CONTROL_TOOLS` | Keep `include_transfer_reason=False` at first, so the declaration is unchanged. Turning it on is a separate, measured change: it could reduce delegation ping-pong. |
| Tool callbacks (C4) | `before_tool_callback` runs per tool execution, not in the prepare phase (650); contextvars set in it now reach the tool (651); callback chains stop at the first non-None result (646). | `adk/callbacks.py`, `adk/mcp_observability.py`, `adk/media_results.py`, `adk/a2ui_*_render.py` | We compose one callable (`adk/agent.py` ~1265–1335), so 646 likely doesn't bite. 651 could *help* the C4 tracker. Re-verify the `LatencyTracker` binding test on both SSE paths. |
| Finality (C1) | An error event without function calls is final (728); the framework holds tool calls that need confirmation (436/438); AgentTool runs unary (912). | `adk/elicitation.py` `make_elicitation_result`; `adk/agui.py` | Run the elicitation and handoff e2e journeys 10/10. |
| Model layer (C3) | Models declare capabilities; output-schema-with-tools is decided from the declaration, not the model id (961/995/997/1006); provider-prefixed Gemini ids (1529). | `adk/resilient_llm.py` (`ResilientLlm(BaseLlm)`) | `ResilientLlm` declares the intersection of its members' schema+tools capability: ADK builds one request before fallback chooses a member. A union or the primary alone can send an unsupported request to fallback. The retry loop already catches `Exception`; direct wrapper tests preserve exact cancellation/interrupt objects. |
| Sessions | `InMemorySessionService` raises `SessionNotFoundError` (598); `VertexAiSessionService` retries once on 429 (700); `Runner.close()` flushes the session service; session TTL (1511). | `adk/session.py`, `adk/resilient_session.py`, fixtures, `scripts/probe_parallel_fanout.py`, `scripts/verify_visual_evidence.py` | Remove one retry layer. Keep ours: it has stall detection and notices. Disable ADK's if configurable; otherwise cap ours at N-1. Fix fixtures. |
| Instructions | `${var}` / `\{var}` left literal (356); state injection skipped without placeholders (1662). | Skill instructions via `compose_instruction_providers` / `wrap_with_platform_preamble` | `test_instruction_template_literals` decides. Audit seeded `SKILL.md` files for `${`. |
| Live | Live mode runs model callbacks (805); audio modules deprecated (357). | `adk/live_agent.py`, `budget/enforcer.py`, `observability/timing.py` | Callbacks must tolerate live requests: no text assumptions. Budget enforcement now *applies* to live, which is probably desired. |
| Skills | `google.adk.skills.models.Skill` gains an origin field (1012). | `skills/skill_materializer.py` | Construct by keyword; set the origin. |
| Telemetry | Duration metrics ms → s (1807); `gen_ai.tool_definitions` → `gen_ai.tool.definitions` (2111); metric attributes removed (2060); OTel event logger removed (1449). | `observability/llm_metrics.py`, `telemetry.py`, `trace_export.py` + dashboards/alerts | Diff against the 01 telemetry list. Update dashboards in the same PR, or they go silent with no error. |
| Security | OAuth secrets no longer leak via `/run`, `/run_sse` and the session endpoints (167). | `fast_api_app.py` (`get_fast_api_app`) | Free fix. Note it in the release notes. |
| Eval | `AgentEvaluator` raises `ValueError` when no cases ran (355). | `make eval` | Make sure the evalset paths resolve. |

### Exit

The shared exit criteria from the hub, plus the cross-version session round-trip in both directions. Record the reverse-read result in the hub's rollback line.


### 03b review evidence (2026-10-23)

This security PR combines the required 02 transport compatibility work with 03b:
`ag-ui-adk==0.10.0`, backend protocol 1.0, `@ag-ui/client`/`core` 1.0.3,
`a2ui-agent-sdk==0.2.2`, ADK runtime/eval 2.12, and aiplatform 2.6.
MCP stays on 1.x, A2A on 0.3, and the existing LiteLLM 1.85.7 pin,
AILANG Parse floor and dependency security floors are preserved. The compaction
helpers already owned in-tree remain unchanged.

Reviewed contract changes:

- Handoff parameters now use JSON Schema. The same required `agent_name`, enum
  targets and floor-policy key equality remain enforced; no transfer reason is enabled.
- A single producer task iterates and closes the AG-UI generator in its own
  Context. Backpressure, disconnect cleanup, progress flushing, RUN_STARTED-first,
  terminal deduplication and the empty-run error remain covered. Null JSON Patch
  values survive typed protocol 1.0 serialization.
- Unknown-tool placeholders are rejected before any composed before callback.
  This preserves balanced callbacks and a visible RUN_ERROR naming the tool;
  an unspecified model explanation is not substituted for the failure notice.
- After-model replacements now retain usage and streaming flags. Budget accounting
  reads the model callback response, not the resulting session event, so this
  preservation does not add a second accounting call.
- `${var}` and escaped braces stay literal; `{var}` still substitutes. The only
  seeded SKILL.md dollar-brace occurrence is a comment about an API-key placeholder
  in maps-assistant, not a state interpolation dependency.
- Orphan in-memory appends now raise SessionNotFoundError; the positive real-Runner
  create-before-append control still persists. ADK Runner consumes root cancellation,
  while the wrapper itself propagates the exact cancellation/control-flow object
  and never tries fallback or emits a retry notice.
- Stock ADK now includes tool results in summaries. The former negative control
  records that improvement; the platform's other fidelity assertions remain.
- ADK's new agent-identity and telemetry configuration routes are admin-guarded.
  Channel event classification covers the new nested-agent lifecycle vocabulary.
  Tool result content parts become readable Activity text; session lookup failures
  show a visible retryable error.

Reproduce the real cross-language wire check (also wired into frontend CI):

```sh
(cd backend && uv run --frozen python scripts/capture_agui_wire.py) > /tmp/agui-wire.jsonl
node frontend/scripts/check-agui-wire.mjs /tmp/agui-wire.jsonl
```

It captures a synthetic real Runner → ADKAgent → platform serializer turn, then
uses the installed HttpAgent SSE parser, reducer and terminal state machine.
The client applies the tool result, null selection patch, answer and one terminal.
No model API, credentials or customer data are involved.

Local checks on the review branch:

| Check | Result |
|---|---|
| `make adk-conformance` | 69 passed, 2 skipped |
| Backend `make lint` | Ruff check and format pass |
| Backend `make test-fast` | 4573 passed, 52 skipped, one existing failure below |
| Frontend `npm run quality:check` | Lint/typecheck, 1821 tests passed (10 skipped), production build passed |
| Captured backend stream → installed HttpAgent 1.0.3 | Pass: tool result, null patch, answer, one terminal |
| `scripts/security-check.sh` | Pass under existing policy/exceptions |
| `make publish-check` | Pass: 1871 published, 56 withheld |

 The unmodified dev baseline
already fails `test_every_registered_tool_is_classified` for
`get_daneel_context` and `handoff_to_daneel`; this PR does not invent a document
scope policy for those tools. `make publish-check` passes. `make template-parity`
cannot run because its referenced `scripts/template-parity.sh` is absent from
platform-source. The runtime security audit passes under the existing documented
exceptions, without adding a new exception.

Telemetry source review found platform-owned `llm.cost.total` (USD),
`llm.tokens.total` (tokens), reliability counters and tenant span attributes, all
independent of ADK's renamed native duration/definition metrics. No tracked
infrastructure dashboard/alert query consumes those old ADK names. External
Cloud Monitoring dashboards and live export still require deployed verification.

**Not a deployment sign-off.** The upstream 10/10 journeys, before/after eval and
latency baseline, Agent Engine session round-trip in both directions, external
telemetry dashboards and previous-image rollback drill remain pending. Treat
ADK 2 session writes as one-way until those checks pass in the target environment.
Keep the PR draft until the shared exit criteria are met; no environment is
promoted by this PR preparation.
