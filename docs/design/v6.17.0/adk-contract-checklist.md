# ADK Contract Checklist

**Status**: Living reference (v6.17.0 adk-contract-conformance)
**Last Updated**: 2026-07-22

Our custom layer rides Google ADK's control-flow. Every recurring bug in this
codebase is the same shape: **we assumed ADK behaves one way; it behaves
another.** This is the list of contracts we've been bitten by. Check it when you
add or change anything that touches a callback, a tool result, the model layer,
the AG-UI stream, or a handoff.

**How to use:** before merging code near any seam below, confirm you honor the
contract, and confirm a guard test exists that would FAIL if you break it. The
guard pattern is a hermetic "real ADK flow" test — a stub `BaseLlm` driving the
real `Runner` (template: `backend/tests/unit/test_handoff_loop_termination.py`).
Run the whole family with `make adk-conformance`. **jsdom/isolated unit tests do
NOT count** — every bug below passed those and failed only on the real flow.

---

### C1 — A tool/callback that "asks and waits" must end the turn (`skip_summarization`)

- **Symptom:** a confirm card / needs-input form is emitted over and over; a lite
  front door re-issues the same `transfer_to_agent` every round (2026-07-22 spam loop).
- **Contract:** ADK's flow (`base_llm_flow.run_async`) re-invokes the model after a
  function response UNLESS the turn `is_final_response()` — which is true iff
  `actions.skip_summarization` is set (or the tool is long-running). ADK's own
  `get_user_choice` sets the flag. An elicitation is a wait-for-the-user boundary.
- **Guard:** `make_elicitation_result` sets `skip_summarization`;
  `test_handoff_loop_termination.py` (stub always transfers → 1 call with the fix, 7 without).
- **Ref:** `handoff_confirm_spam_loop_skip_summarization`; `events/event.py` `is_final_response`.

### C2 — ADK artifact/session calls are coroutines — `await` them

- **Symptom:** `save_artifact` silently never writes → `retrieve_artifact` 404s (broke document fetch).
- **Contract:** `save_artifact`/`load_artifact` (and session mutators) are async. An
  un-awaited coroutine is a no-op that raises nothing. Mock tests can't catch it;
  a real save→load roundtrip can.
- **Guard:** a real-flow test that a callback's `save_artifact` result is retrievable via `load_artifact`.
- **Ref:** `adk_async_callbacks_must_be_awaited`.

### C3 — A `ResilientLlm`/fallback member must rewrite `llm_request.model` per member

- **Symptom:** every fallback target 404s — a Gemini fallback is called with the
  primary's `claude-opus` id (Vertex anthropic publisher 404).
- **Contract:** ADK stamps the primary's model id onto `llm_request.model` once. A
  fallback member must rewrite it to its own id before calling, or it inherits the
  wrong one.
- **Guard:** a real-flow test asserting the model id is rewritten per member across a simulated primary-404 → fallback.
- **Ref:** `resilient_llm_rewrites_model_per_member`. Sibling seam: the raw-genai
  tool path must ALSO pin `location=global` for global-residency models
  (`resilient_genai_must_pin_global_location`).

### C4 — Any SSE endpoint running `stream_agui_events` must bind/reset the `LatencyTracker`

- **Symptom:** "A2UI won't render in the Workspace" — the agent narrates "I updated
  the Workspace" but the tab stays empty; `artifactCount` stays 0.
- **Contract:** `emit_a2ui_surface` enqueues onto a per-request `LatencyTracker`
  bound to the async context via `set_current_tracker`. Without the bind,
  `get_current_tracker()` returns the module NULL tracker and every emit silently
  no-ops. `stream_skill` binds/resets it; any new SSE endpoint must mirror that.
  (Also: never emit A2UI from inside an `AgentTool` sub-agent callback — separate
  Runner, unbound tracker.)
- **Guard:** a real-flow/stream test that `A2UI_SURFACE` emits under a bound tracker and *visibly* no-ops without it.
- **Ref:** `a2ui_workspace_render_trap`; `backend/adk/CLAUDE.md`.

### C5 — A tool param with no default is `required` — make it optional + return `needs_input`

- **Symptom:** asked with a param missing, the model INVENTS a value (wrong-year
  answer) or INTERROGATES the user for 5 turns instead of calling the tool.
- **Contract:** ADK marks any no-default param `required` in the generated
  `FunctionDeclaration`. Both halves of the fix are needed: (a) make params optional
  and return a `needs_input` envelope when missing; (b) tell the model in the tool
  DOCSTRING to call it bare ("never invent a value to fill the call").
- **Guard:** a live stream (unit tests can't see this — the tool DOES return a form;
  the model just never called it).
- **Ref:** `required_params_force_invent_or_interrogate`.

### C6 — `transfer_to_agent` is a control baton (one at a time), not fan-out

- **Symptom:** two "Delegated to X" chips for one query; a later transfer's tool body
  overwrites `actions.transfer_to_agent` (nondeterministic winner).
- **Contract:** `transfer_to_agent` is a single control handoff. Two in one response
  is undefined. The parallel primitive is `AgentTool` (parent stays in control, fires
  many concurrently, synthesizes). A handoff guard must scope to `transfer_to_agent`
  ONLY, never `AgentTool`.
- **Guard:** (parked residual) an `after_model` strip of all-but-first
  `transfer_to_agent`; verify with a real AG-UI stream counting `AGENT_DELEGATION`.
- **Ref:** `handoff_multi_transfer_research`.

### C7 — `RUN_STARTED` must be the first AG-UI event

- **Symptom:** `@ag-ui/client` rejects the stream — "Cannot send event ... before RUN_STARTED".
- **Contract:** any pre-agent CUSTOM events (`MODEL_RESOLVED`, heartbeats) must be
  BUFFERED and flushed AFTER `RUN_STARTED`. Terminal events (`RUN_ERROR`/`RUN_FINISHED`)
  must be LAST and singular.
- **Guard:** verify with a REAL stream (jsdom passes while the live client rejects).
- **Ref:** `agui_run_started_must_be_first`.

---

## When ADK is upgraded

The `google-adk` pin (`backend/pyproject.toml`) exists because these contracts can
shift between minor versions. To bump it:

1. Change the pin (both entries) → `uv lock` → review the lock diff.
2. `make adk-conformance` must stay green (the guards above).
3. Post-deploy: `make handoff-e2e ENV=dev` on a live stream.

A red guard after a bump means ADK changed a contract we depend on — fix the seam,
don't loosen the guard.
