# ADK 2 upgrade · 01 — Regression gate

**Status**: Proposed
**Phase**: 0. It blocks every other sub-doc.
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-16

## Goal

Before any version moves, pin today's behaviour well enough that a bump that breaks a journey **fails CI or a scripted check**, not a user. Our recurring bugs passed unit and jsdom tests and failed only on the real ADK flow (see the [contract checklist](../v6.17.0/adk-contract-checklist.md)). The gate therefore has to drive the real `Runner` and the real deployed stream.

## What exists

- `make adk-conformance` (`pytest -m adk_contract`) runs 9 real-flow tests: handoff loop termination (C1), artifact round-trip (C2), `ResilientLlm` flow (C3), A2UI emitter tracker binding (C4), AG-UI turn finality, and four compaction tests.
- `aiplatform skill chat` / `skill probe` for headless journeys against a deployed env.
- `make handoff-e2e`, `make elicitation-e2e`, `make model-fallback-e2e`, `make smoke-long-stream`.
- `make eval` (`adk eval`) with existing evalsets.

## Gaps this sub-doc closes

Each new test is a hermetic "real ADK flow" test: a stub `BaseLlm` driving the real `Runner` (template: `tests/unit/test_handoff_loop_termination.py`), marked `adk_contract`. Each one targets a changelog entry between 1.31.1 and 2.12.0 that touches a seam we customise. Line numbers refer to the ADK changelog.

| New contract test | Pins | Changelog risk it guards |
|---|---|---|
| `test_transfer_declaration_shape` | The `transfer_to_agent` `FunctionDeclaration` our enum guard produces: parameters, enum, required. Snapshot it. | 609: `transfer_reason` argument (opt-in `include_transfer_reason`). 652/487: transfers restricted to declared targets. |
| `test_before_tool_callback_contextvars` | A contextvar set in our `before_tool_callback` is (or isn't) visible inside the tool, and the callback runs once per tool call in a parallel batch. | 650/651: callback moved from the prepare phase into per-tool execution; contextvars now propagate. |
| `test_error_event_finality` | What `is_final_response()` returns for an error event with no function calls, and that our AG-UI terminal handling emits exactly one terminal event. | 728 |
| `test_hallucinated_tool_name` | A model call to an unknown tool: our callbacks fire balanced, and the user sees a visible notice (CLAUDE.md #8). | 632/693: now reported to the model instead of raising. |
| `test_instruction_template_literals` | A skill instruction containing `{var}`, `${var}` and `\{var}` renders exactly as today. | 356/1662: templating no longer substitutes these, and state injection is skipped when there are no placeholders. |
| `test_after_model_replacement_metadata` | A response replaced in `after_model_callback` keeps the `usage_metadata` and streaming flags our latency and billing code read. | 101/437 |
| `test_inmemory_append_unknown_session` | Local mode and test fixtures create sessions before appending. | 598: `SessionNotFoundError` |
| `test_resilient_llm_interrupt_passthrough` | `ResilientLlm`'s `except BaseException` (`adk/resilient_llm.py:143`) re-raises cancellation / `NodeInterruptedError`-class exceptions. | ADK 2.0 migration-guide warning |

### Results: tests 1–4 (2026-10-16)

Tests 1–4 are in `backend/tests/unit/` and marked `adk_contract`. Tests 5–8 are not written yet.

| Test file | 1.31.1 (`.venv`) | 2.12 venv (`.venv-adk212`), one run |
|---|---|---|
| `test_transfer_declaration_shape.py` | 2/2 pass | **Collection error.** It never ran |
| `test_before_tool_callback_contextvars.py` | 3/3 pass | 3/3 pass |
| `test_error_event_finality.py` | 3/3 pass | 2/3 pass. **The AG-UI wire test fails** |
| `test_hallucinated_tool_name.py` | 3/3 pass | **0/3 pass** |

What each test pins on 1.31.1:

- **Transfer declaration.** `transfer_to_agent` declares exactly one parameter, `agent_name: STRING`, which is required. Its enum lists the sub_agent slug names in sub_agent order. The enum equals the floor policy's `delegate_map` keys. The declaration uses the `parameters` form, not `parameters_json_schema`.
- **Contextvars.** In a 3-call parallel batch, the composed `before_tool_callback` runs once per call. Each tool sees the contextvar value that *its own* callback set, and never a sibling call's value. Nothing leaks back to the Runner's caller.
- **Error finality.** An `error_code` event with no function calls has `is_final_response() == True`, and the model is called once. Through the real ag_ui_adk 0.6 `ADKAgent` and `stream_agui_events`, the wire shows `RUN_STARTED` first. It ends with exactly one terminal event, a `RUN_ERROR` with a message: the never-silent `EMPTY_RUN` rewrite.
- **Hallucinated tool.** ADK raises `ValueError("Tool '<name>' not found…")` out of the Runner. We wire no `on_tool_error_callback`. None of our tool callbacks fire, so they are balanced at 0/0. On the AG-UI wire, the single terminal event is a `RUN_ERROR` whose message names the tool.

How the 2.12 run failed. The `.venv-adk212` venv carries the whole forward stack, not just ADK: ag-ui-adk 0.9.0, ag-ui-protocol 1.0.0, mcp 2.3.0, a2ui-agent-sdk 0.2.4. Two of the three failure causes come from those packages, not from ADK. Each item below says which sub-doc owns the fix.

1. **`test_transfer_declaration_shape`: import error, not a contract result (owner: 03).** `adk.agent` imports `adk.a2ui`, which imports `a2ui.adk.send_a2ui_to_client_toolset`. That module raises `NameError: name 'models' is not defined` at import (`send_a2ui_to_client_toolset.py:281`): it annotates `models.LlmRequest` but imports only `LlmRequest`. Every module that imports `adk.agent` fails the same way on that venv. **So the transfer declaration's shape on 2.12 is still unknown.** Re-run it once the a2ui pin is fixed. That re-run checks changelog 609/652/487.
2. **`test_error_event_finality::test_agui_wire_has_exactly_one_visible_terminal_event` (owner: 02).** This fails before any terminal event is checked. ag_ui_adk 0.9 sets a session-read-cache ContextVar in its run generator before the first `yield` (`adk_agent.py:2940`). It resets the ContextVar in a `finally` after later yields (`:3081`). Our `stream_agui_events` drives each step with `asyncio.ensure_future(anext(source))` (`adk/agui.py:316`). Each step therefore runs in a fresh Context copy, and the reset raises `ValueError: <Token …> was created in a different Context`. Every real chat turn will hit this, not just this test. The fix belongs to **02**: the AG-UI bump must either stop wrapping `anext` in a new task per step or carry one Context across steps. The two Runner-level tests pass on 2.12: an error event is still final and still ends the turn (changelog 728 changes nothing for us).
3. **`test_hallucinated_tool_name`, all three tests (owner: 03).**
   - `test_unknown_tool_raises_out_of_the_runner`: `DID NOT RAISE`. As changelog 632/693 says, 2.12 logs `Tool 'search_the_web' not found` and reports it to the model. The turn continues, and the model was called again. This test pins the 1.31.1 mechanism and is expected to go; 03 replaces it.
   - `test_tool_callbacks_fire_balanced`: **a real contract break.** `before=['search_the_web'] after=[]`. On 2.12, our `before_tool_callback` chain now **runs for the unknown tool**, and no `after_tool_callback` follows. In production this means the permission enforcer's `Calling search_the_web…` label and the MCP span tagger run with no paired after-hook. 03 must either skip unknown tools in the before-chain or accept and pair them.
   - `test_user_sees_a_visible_notice_naming_the_tool`: the same ag_ui_adk 0.9 ContextVar failure as item 2, so the visible-notice contract is still unverified on 2.12. Note that on 2.12 the notice now depends on what the model says after the reported error, not on a `RUN_ERROR`. 03 should decide whether we emit our own notice.

`test_before_tool_callback_contextvars` passes unchanged on 2.12. For our callbacks, 650/651 make no observable difference: one call per parallel tool call, and the callback-set contextvar reaches only its own tool.

**Work this hands to 03 (and 02):** fix the a2ui-agent-sdk import and re-run test 1 (03); make `stream_agui_events` Context-stable for ag_ui_adk 0.9 (02); handle unknown-tool before-callbacks and decide the user notice (03).

## Cross-version session round-trip (scripted, dev only)

`make adk-session-roundtrip FROM=<ver> TO=<ver>` (new; script under `scripts/`):

1. With the FROM image, create a session on dev Agent Engine, run 3 turns (including a tool call and an artifact save), and record the session id.
2. With the TO image, `get_session`, append a turn and load the artifact.
3. **Reverse:** with the FROM image, read the session that TO wrote. Record whether it works. The result decides whether rollback is safe, and goes into the hub's rollback line.

## Baselines to capture once, before 02

- **Journey pass rates**: 10 runs each, recorded in this doc. Run with `aiplatform --env dev skill chat`: greeter handoff, confirm-handoff, an MCP skill, a document fetch, a compaction-triggering long turn, browser, an A2UI render on the chat path, and an A2UI render via `surface-action-run`.
- **Eval**: `adk eval` scores, plus first-token latency (TTFT) and turn duration from `skill probe`, run 10×.
- **Telemetry**: list the metric and attribute names our dashboards and alerts read. ADK 2.x renames several, in 03; this list is what 03 diffs against.

## Exit criteria

- 8 new `adk_contract` tests green on 1.31.1. Run each new test against an ADK 2.12 venv once and record which ones fail; that list becomes 03's work.
- The round-trip script runs FROM=TO=current on dev.
- Baselines are recorded in this doc.

## Episode tps-b — contracts 5–8 (2026-10-16)

Added four hermetic, module-marked `adk_contract` files in
`backend/tests/unit/`, following `test_handoff_loop_termination.py`:
stub `BaseLlm`, real `LlmAgent` / `Runner`, SSE streaming and in-memory
sessions. No dependency or production-code changes.

Each file was run with `adk-contract-file tests/unit/<file>.py` against
ADK 1.31.1 and with `adk212-contract-file tests/unit/<file>.py` against
ADK 2.12.0. The table records the final current-version result and the
single completed 2.12 run per file; all 2.12 failures are runtime behavior
differences, not collection failures.

| File / contract | 1.31.1 | 2.12.0 | Observed difference / work for 03 |
|---|---|---|---|
| `test_instruction_template_literals.py` | PASS (1 test) | FAIL (assertion) | With state `var=VALUE`, instruction `{var} \| ${var} \| \{var}` becomes `VALUE \| $VALUE \| \VALUE` today; 2.12 produces `VALUE \| ${var} \| \{var}`. Both include the same ADK agent-identity suffix, which the snapshot also pins. Audit skill strings using dollar/escaped braces before changing the expectation. |
| `test_after_model_replacement_metadata.py` | PASS (1 test) | FAIL (assertion) | A callback returning a new content-only response loses `usage_metadata`, `partial`, and `turn_complete` today: all are `None` in both resulting events. 2.12 preserves usage (prompt=11, candidates=7, total=18) and flags `(True, False)` / `(False, True)`. This is an upstream improvement; review latency/billing and accept the new snapshot in 03. |
| `test_inmemory_append_unknown_session.py` | PASS (1 test) | FAIL (`SessionNotFoundError: Session missing not found.`) | Today append returns the event for an unregistered `Session` but does not create a stored session. 2.12 raises at `append_event`. A positive control creates a session before running a real turn and verifies persistence on 1.31.1; the 2.12 failure occurs before that control. Audit local fixtures for create-before-append. |
| `test_resilient_llm_interrupt_passthrough.py` | PASS (1 test, four cases within it) | FAIL (`DID NOT RAISE CancelledError`) | On 1.31.1 the exact cancellation / direct-`BaseException` interrupt object reaches the caller, with one primary call, no fallback and no reliability notices, both with and without the first-token deadline. On 2.12 the first case (cancellation, deadline disabled) returns without raising; Runner logs `Root node skill was cancelled by an external cancellation.` Later cases are not reached. Review Runner cancellation semantics in 03; this result does not establish that the wrapper swallowed the signal. |

The interrupt stand-in models the direct-`BaseException` inheritance of
ADK 2 control-flow interrupts; it does not import a 2.x-only exception into
the 1.31.1 gate. Inspection also found that the current retry loop catches
`Exception`; the broad `BaseException` handler referenced in the original
plan is in first-token timeout cleanup.

These snapshots deliberately pin 1.31.1, including existing metadata loss.
They have no version branches, skips or xfails. Tests 1–4, cross-version
session round-trip and all deployed journey/eval/telemetry baselines are
outside this episode. Phase 0 remains incomplete until those separate
deliverables satisfy the exit criteria above.
