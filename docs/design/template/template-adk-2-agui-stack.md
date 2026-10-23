# ADK 2 upgrade · 02 — AG-UI stack (ag-ui-adk 0.9, protocol 1.0)

**Status**: Implemented with the 03b security PR for review; deployed gates pending
**Phase**: 1, first step. Runs on ADK 1.31.1 and must land before 03b, because ag-ui-adk 0.6.0 caps `google-adk<2`.
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-23

## Scope

| Package | From | To |
|---|---|---|
| `ag-ui-adk` (backend) | 0.6.0 | 0.9.0. It requires `google-adk>=1.28.1,<3` and `ag-ui-protocol>=1.0.0` (PyPI metadata). |
| `ag-ui-protocol` (backend, transitive) | 0.1.x | ≥1.0.0 |
| `@ag-ui/client`, `@ag-ui/core` (frontend) | ^0.0.52 | 1.0.x (1.0.3 latest) |

This must be **one PR**. The backend and frontend protocol versions move together.

## Our touch points

- `backend/adk/agui.py`: `ADKAgent.from_app(...)` with `use_in_memory_services=True` and `session_timeout_seconds=86400`. `stream_agui_events` buffers pre-agent CUSTOM events and flushes them after `RUN_STARTED` (contract C7).
- `backend/protocols/agui.py`
- `frontend/src/providers/AGUIProvider.tsx`, `frontend/src/hooks/useSkillAgent.ts`, `frontend/src/hooks/useActionDrivenAgent.ts`

## Breaking changes from the ag-ui-adk changelog that hit us

| Release | Change | Action |
|---|---|---|
| 0.7.1 | Session-lookup read failures end the run with `SESSION_LOOKUP_ERROR` instead of silently creating a replacement session. | **Good for "never silent" (#8).** The frontend must render this code as a visible error with a retry, not a blank turn. Add a case to the AGUIProvider error mapping. |
| 0.7.1 | `mark_messages_processed` / `get_processed_message_ids` scope becomes keyword-only (`app_name`, `user_id`, `thread_id`); `session_id` → `thread_id`. Lookup helpers require explicit `app_name`. | Grep for any direct calls. Pass `APP_NAME` from `adk/agui.py`, never a literal. |
| 0.7.1 | `get_session` contract tightened for custom session services (return `None` when absent). | `ResilientVertexSessionService` (`adk/resilient_session.py`) must return `None`, not raise, on not-found. Add a unit test. |
| 0.7.1 | CORS no longer reflects arbitrary origins when credentials are enabled. | Re-verify the frontend→backend proxy and any direct cross-origin callers (MCP sandbox, A2A). |
| 0.7.1 | Framework adapters moved to the 1.0 models (part renames, content flattening). | Re-verify tool-result unwrap (`src/lib/toolResult.ts`, the double-wrap hazard) and file parts. |
| 0.8.0 | `RUN_STARTED` carries `protocolVersion`; `exclude_none` removed from SSE serialization (`null` patch values retained). | Our buffered flush must preserve `RUN_STARTED` exactly as emitted, and any synthetic `RUN_STARTED` we build must include `protocolVersion`. The frontend client 1.0 expects it. |
| 0.8.0 | Failed session reads end the run instead of creating a replacement. | As 0.7.1 above. |
| 0.9.0 | The processed-message ledger lives in session state (`_ag_ui_processed_message_ids`) and rides `run_async(state_delta=…)`. | Prevents duplicate turns across Cloud Run instances. Check that state-size growth on long threads is bounded. Hide the key from any state inspector / admin view. |
| 0.9.0 | State-delta patches are typed JSON Patch operations, not dicts. | Any backend code comparing patches calls `model_dump()`. |
| 0.7.1+ | `resume[]` input and opt-in `emit_interrupt_outcome`. | **Leave `emit_interrupt_outcome` off.** With it on, `@ag-ui/client` rejects a run that answers an interrupt with a plain tool message, which is how our confirm card answers today. Adopting `resume[]` belongs with the `ToolConfirmation` decision in [07](template-adk-2-native-replacements.md). |

## Gains

- Session-read cache (0.7): fewer Agent Engine `get_session` round-trips per turn, which goes straight at first-token latency.
- Duplicate-turn prevention across replicas (0.9).
- Single terminal event across history batches; the run ends after a batch `RUN_ERROR` (0.7.1/0.8). This may let us delete the duplicate-terminal guard added for template item G41. Verify before deleting.
- Thought-signature `REASONING_ENCRYPTED_VALUE` passthrough (0.7.1), relevant to the Gemini 3.x thought-signature migration.

## Tests

- C7 contract (`test_agui_turn_finality`, the `RUN_STARTED`-first checks), using a real 1.0 client parse. A new Node script in `frontend/scripts/` feeds a captured SSE stream through `@ag-ui/client` 1.0. jsdom alone is not enough.
- Force a session-lookup failure → the frontend shows a visible error (headless: `aiplatform skill chat` prints `RUN_ERROR SESSION_LOOKUP_ERROR`; browser: a real Chrome session).
- Confirm-handoff journey 10/10. This is the plain-tool-message answer path.
- Two concurrent turns on one thread across two instances on dev → one turn, not two.


## Security PR implementation (2026-10-23)

The required bridge work is combined with 03b to reach the patched OTel stack:
backend ag-ui-adk 0.10.0 / protocol 1.0 and frontend client/core 1.0.3.
The Context-stable producer fixes the per-anext ContextVar crash, null state
patches remain valid JSON Patch, structured tool results stay readable, and a
SESSION_LOOKUP_ERROR is visible and retryable. A captured real backend turn
passes through the installed 1.0.3 client in CI; see the commands and reviewed
contracts in [03b](template-adk-2-core-bump.md#03b-review-evidence-2026-10-23).
The original proposal to land 02 separately is superseded for this security PR;
its live/deployed exit criteria still apply and are not claimed complete.
