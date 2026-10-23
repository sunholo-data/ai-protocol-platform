# ADK 2 upgrade · 07 — Native replacements for homegrown code

**Status**: Proposed
**Phase**: 2, after 03b. Each row is its own PR, and the rows are independent.
**Estimated**: 3–5 days across the rows that are adopted
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-16

## Principle

We built several things ADK now ships. Replacing them deletes code and the drift that comes with it, but **only when the native one covers what ours does, and is not `@experimental`**. Our [Bucket-C decision record](../v6.17.0/toolconfirmation-decision.md) set this rule. Every swap lands behind an `adk_contract` test that fails if the behaviour changes. The answer can be "keep ours"; record why and add a revisit trigger.

## Candidates

The experimental status was checked against the 2.12.0 wheel on 2026-10-16.

| Native (version) | Experimental? | Ours | Covers ours? | Recommendation |
|---|---|---|---|---|
| `FallbackModel` (2.9) | **Yes**, `@experimental(FALLBACK_MODEL)` | `adk/resilient_llm.py` `ResilientLlm` | Partly. It fails over on 429/5xx, but only before the first streamed chunk. No retries, no stall detection, no AG-UI notices, no per-member model-id rewrite guarantee (C3). | **Keep ours.** Revisit when it graduates. Meanwhile adopt its *capability declaration* shape for `ResilientLlm` (see 03). |
| `ReflectAndRetryModelPlugin` (2.6) | Private module `_reflect_retry_model_plugin` | `ResilientLlm` retries | Different purpose: reflection on malformed output | Evaluate for tool-call JSON errors only |
| `ToolCallIntegrityPlugin` (2.11) | Private module | none | n/a | **Trial.** It may cover the hallucinated / malformed tool-call class (see the 01 test). |
| `ToolConfirmation` / `request_confirmation` / `require_confirmation` (2.12) / `request_input` (2.2) | **Yes**, `@experimental(TOOL_CONFIRMATION)` | `adk/elicitation.py` `make_elicitation_result` + confirm-handoff switch | No. Its resume model re-runs the tool; ours switches skill and re-issues the request. | **Keep ours** (the decision record's trigger hasn't fired). Pair any future move with AG-UI `resume[]` ([02](template-adk-2-agui-stack.md)). |
| MCP `elicitation_callback` (2.7) | — | `adk/elicitation.py` (generic) | It covers *server-initiated* MCP elicitation, which we don't handle today | **Adopt** as a new capability: route MCP elicitations to our A2UI form path. |
| `SkillToolset` activation, `SkillDiscoveryMode`, unload (2.10), skill registry (1.34), script outputs as artifacts (2.12) | `SkillToolset` no; unload experimental | `skills/skill_materializer.py`, `skills/skill_processor.py` | Partly. Ours carries per-skill access tags, Firestore seeding and delegation. | **Spike** (½d): could our materializer feed `SkillToolset` for on-demand skill loading (progressive disclosure), keeping our access layer in front? |
| Tools returning media to the model (2.7), `multimodal_tool_results_plugin` | — | `adk/media_results.py` | Likely yes | **Adopt** if the parity test passes. Fewer bytes through our own code. |
| Native `gen_ai.client.*` metrics, per-invocation/workflow token metrics (2.2/2.8), `AutoTracingPlugin` (2.2) | — | `observability/llm_metrics.py` | Mostly | **Adopt the native metrics, keep our latency tracker** (it drives AG-UI `LATENCY_REPORT`). Delete the duplicate counters. |
| `to_mcp_server` (2.5) | — | `protocols/mcp_proxy.py`, `protocols/mcp_server.py` | Unknown | Spike after [06](template-adk-2-mcp-2.md) |
| `ContextFilterPlugin` | — | compaction + second pass | No (it is a different mechanism) | Not adopted |
| `ResumabilityConfig(is_resumable=True)` | — | none | n/a | A prerequisite for durable pause/resume in sub-agents (the Horizon `drive_child` pattern). Its own design if we want HITL inside `AgentTool` children. |
| `ModelConsultTool` (2.11) | — | none | n/a | Look into it for "ask a stronger model" inside a lite skill |

## Exit per row

- An `adk_contract` test pins the behaviour before the swap.
- The homegrown code is deleted, not left beside the native one.
- The `make model-fallback-e2e` / `make elicitation-e2e` / `make handoff-e2e` journey for that seam passes 10/10 on dev.
