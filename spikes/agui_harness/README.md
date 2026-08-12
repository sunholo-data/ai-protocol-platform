# AG-UI Streaming Harness (Phase 0 M2 spike)

Standalone SSE probe that validates the `ag-ui-adk` integration end-to-end:
it posts an AG-UI `RunAgentInput` to `/api/chat/spike` and streams back the
AG-UI events, logging each event type + salient fields and writing a JSONL
trace to `last_run.jsonl` for post-hoc inspection.

## Running

### Local

```bash
# Terminal 1 — start the backend
cd backend && make dev

# Terminal 2 — run the probe
cd backend && uv run python ../spikes/agui_harness/probe.py
```

### Against deployed dev

```bash
cd backend && uv run python ../spikes/agui_harness/probe.py --env dev
```

### Custom prompt / URL

```bash
uv run python spikes/agui_harness/probe.py --prompt "hello" --url http://localhost:1956/api/chat/spike
```

## Why is this endpoint unauthenticated?

The `/api/chat/spike` endpoint mounted by `backend/fast_api_app.py` deliberately
has **no Firebase auth wiring**. This is SPIKE-ONLY to let the probe fire without
minting tokens. Phase 1A.5 will:

1. Replace this mount with skill-driven dynamic mounting.
2. Compose Firebase bearer-token middleware (see the "deferred" row in
   `docs/design/v6.0.0/streaming-and-protocols.md` "Verified Event Flow").
3. Remove the `spike_agent` + `mount_skill_endpoint(..., "spike", ...)` call.

**Do not let this endpoint ship to `test` or `prod`.** Before those branches
merge, delete the spike mount from `fast_api_app.py`.

## What this probe proves

- `ag-ui-adk` 0.6.0 accepts a minimal `RunAgentInput` over POST.
- ADK `FunctionTool` calls surface as `TOOL_CALL_*` AG-UI events.
- SSE framing is standard `data: <json>\n\n` — can be consumed with any SSE client.
- The `ADKAgent(session_service=...)` kwarg accepts a `VertexAiSessionService`
  (verified via API inspection; see design doc for verdict).
