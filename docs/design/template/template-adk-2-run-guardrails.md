# ADK 2 upgrade · 08 — Run guardrails (one halt plugin)

**Status**: Proposed
**Phase**: 2, after 03b
**Estimated**: 1–2 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Pattern source**: `google/adk-recipes` Horizon. See `horizon/conversation/iteration_budget_plugin.py`, `graceful_halt.py` and `guardrails_plugin.py`.
**Last Updated**: 2026-10-16

## Problem

Our worst incidents have been runaway turns: the confirm-card spam loop (C1), and multi-transfer ping-pong that regressed into a timeout loop (C6). Each was fixed at its own seam. Nothing **generic** bounds a turn:
- We never set `RunConfig.max_llm_calls`, so ADK's default of 500 applies (`ADK_MAX_LLM_CALLS`, 2.12 `run_config.py`). By then a user has waited minutes and we have paid for it.
- No stop for "no progress": the same tool called with the same args, or the same transfer repeated.
- No stop for repeated failure: N consecutive tool errors.

When a loop does end, it usually ends with a timeout, which breaks "never silent" (#8).

## Design

One ADK plugin (`BasePlugin`; plugins are mature on 2.x, and 2.5 adds `on_agent_error`/`on_run_error`). It is registered on the `App`, so it covers the chat path, `surface-action-run`, channels and A2A alike.

| Halt | Trigger (configurable, defaults shown) | Notes |
|---|---|---|
| `iteration_budget` | > 25 model calls in one invocation | Set `RunConfig.max_llm_calls` a little higher as the hard backstop. |
| `no_progress` | The same `(tool, normalized args)` 3× in an invocation, or the same `transfer_to_agent` target twice | Covers the C1/C6 class generically. |
| `repeated_failure` | 3 consecutive tool errors | — |

- All three set one `halt_reason` in state. They reset together at the turn boundary (Horizon's design).
- On halt, the plugin ends the turn with a **final** response (C1: `skip_summarization`) and emits one visible notice through the existing AG-UI notice path, e.g. "I stopped after N steps without making progress — here's what I tried". It never ends silently.
- `halt_reason` is logged with the session and trace ids, so `aiplatform logs` can find halted turns.
- Per-skill overrides live in SKILL.md config (e.g. a research skill gets a bigger budget), validated at seed time.

## Tests

- `adk_contract` stub-LLM tests: a stub that always calls the same tool, a stub that always transfers, and a stub whose tools always raise. For each, the turn ends in ≤ budget calls with one final event and one notice.
- Headless: `aiplatform skill chat` with a prompt known to loop. The notice is printed; no `RUN_ERROR` timeout.
- Eval rubric (from Horizon's set): "guardrail halting", added to `make eval`.
