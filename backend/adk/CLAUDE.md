# CLAUDE.md — `backend/adk/` A2UI result rendering

Getting a tool result to render as an A2UI surface in the workbench is a
**recurring failure** in this repo — the symptom is always "the agent says it
updated the Workspace but the tab stays empty," and we always re-derive the
path and hunt in the weeds. This file is the playbook. Read it before touching
`a2ui_*_render.py`, `callbacks.py`, or adding any tool that should show UI.

See also: memory `a2ui-workspace-render-trap`, and the authoritative designs
`docs/design/v6.7.0/implemented/tool-results-as-a2ui.md` (7.3) +
`workbench-artifacts-model.md` (7.5).

## Compaction lives here too — read the findings log first

`session.py` (config), `compaction_summarizer.py` (policy) and the per-turn
demotion in `callbacks.py` are the conversation-compaction subsystem. It has its
own recurring-failure playbook, kept version-independent because the findings
outlive any one version:

**[docs/projects/compaction/README.md](../../docs/projects/compaction/README.md)**

Read it before touching any of those three files. The short version of why:
compaction is **lossy and invisible** — the summary replaces the raw turns in
the model's request while the UI keeps showing the full transcript, so a
degraded answer looks identical to a good one. It was also completely INERT for
months while its unit tests passed, because nothing checked the config reached
the code that reads it. The log carries the mechanism map, the measured numbers,
nine traps, and what is still unproven.

## The golden rule — don't invent, register a mapping

A tool's result becomes UI in **exactly one place**: a result→A2UI transform
registered in `a2ui_result_render.register(...)`. Never write bespoke emission,
never author A2UI from an agent prompt (Model B), never add per-tool React.
The **template to copy is `a2ui_ppa_render.py`** (`_Tree` builder + `register`).

```python
register(
    my_transform,                 # (typed_result, tool_context=None) -> messages | None
    tool_names=["my_tool"],       # also marks the tool offload-EXEMPT (see trap 4)
    surface="my_surface",         # literal, or a callable typed_result->surfaceId (per-entity)
    artifact_meta=lambda r: {"kind": "...", "title": "...", "description": "..."},  # -> own tab + Home index (7.5)
)
```

Then import the module for its side-effect registration in `agent.py`
(alongside `_a2ui_ppa_render`) so it registers at startup. `artifact_meta` is
what gives a result **its own workbench tab + a row in the Workspace/Home
index** — declare it and the tab appears for free.

## The traps (each has bitten; miss one → silent no-op, no error)

1. **Emit only from the MAIN agent's `after_tool_callback`**
   (`make_a2ui_result_emitter`). **NEVER emit A2UI from inside a sub-agent
   (`AgentTool`) callback** (e.g. a search sub-agent's `after_model_callback`).
   `AgentTool` runs the sub-agent in a *separate `Runner` with its own
   `InMemorySessionService`* (`agent_tool.py` `run_async`), where the
   per-request `LatencyTracker` ContextVar is NOT bound → `emit_a2ui_surface`
   hits the module NULL tracker and vanishes. (2026-07-16 web-search Sources
   bug.) A sub-agent's side-data reaches the parent via
   `tool_context.state['temp:_adk_grounding_metadata']` (set by
   `AgentTool(propagate_grounding_metadata=True)`) — read it there in the
   transform, then render on the main path.

2. **Any new SSE endpoint running `stream_agui_events` MUST bind the tracker.**
   `set_current_tracker(LatencyTracker(...))` before the generator,
   `reset_current_tracker(...)` in `finally` — mirror `fast_api_app.py`
   `stream_skill`. Without it every `emit_a2ui_surface` silently no-ops.
   (2026-07-11 `surface-action-run` bug.)

3. **The emitter skips non-JSON results.** `_coerce_typed_result` returns
   `None` for free text, so a text-returning tool won't render UNLESS it is a
   registered (mapped) tool — then the emitter renders from context
   (`typed={}`) and the transform reads `tool_context.state`. If your tool
   returns prose, register a mapping and read side-data from state.

4. **Two wire hazards + offload.** Results are double-wrapped
   `{"result":"{…}"}` (peeled server-side by `_coerce_typed_result`, client by
   `src/lib/toolResult.ts`), and >50K results are offloaded to an artifact by
   `_handle_large_output`. Declaring `tool_names` on a mapping marks the tool
   render-payload (offload-EXEMPT) so the render isn't stranded.

5. **`surfaceId` must be consistent.** The transform builds messages with an
   inner `surfaceId`; `render_for_emit` retargets them to the mapping's
   resolved `surface`. Don't hardcode a different id in the messages.

## Tool results are PRIVILEGED at the stream boundary (v6.19.0)

`stream_invariants.redact_privileged_results` withholds `TOOL_CALL_RESULT`
payloads from **lower-trust sessions** (anonymous-group). It matters here
because the allowlist is the same registry this file is about:

- A tool with a **registered result→A2UI mapping is automatically
  client-visible** — `is_render_payload_tool` drives both the offload exemption
  (trap 4) and this gate. Register the mapping and rendering keeps working.
- An **unregistered** tool's result is withheld from group sessions, and an
  **unpairable** result (no matching `TOOL_CALL_START`) is withheld too — it
  fails closed by design.
- Surfaces are unaffected: `A2UI_SURFACE` CUSTOM events pass through untouched,
  so the server-side emitter path renders normally either way.

So the symptom "my tool's result shows in the Activity chip for me but is
`[redacted…]` for a group user" is this gate, working. Register the mapping.

## Verification (non-negotiable)

**jsdom / pytest passing ≠ it renders.** Confirm the `A2UI_SURFACE` CUSTOM event
actually emits on a **real** AG-UI stream (`aiplatform skill ...`) AND the
frontend `SurfaceRegistry` registers it — split backend-emission from
frontend-render. Preview a transform headlessly with
`aiplatform a2ui render <mapping> --result <file.json>`.

## Agent-behaviour gotchas (each shipped a real bug on 2026-07-17)

These are NOT prompt problems. Each was diagnosed as the model correctly obeying
something we declared, and each cost a user-visible defect. Prompting harder does
not fix any of them.

### 1. REQUIRED tool params force the model to invent or interrogate

A param with no default is `required` in ADK's generated `FunctionDeclaration`.
That leaves a well-behaved model exactly two legal moves when the user hasn't
supplied it: **invent a value**, or **interrogate the user in prose**. Both shipped:

```
entsoe_day_ahead_prices -> required: ['bidding_zone','start_date','end_date']
  => asked for "start of year to now" it QUERIED 2024 (invented) and reported it
  => asked "can you query prices?" it spent 5 TURNS collecting 3 values
map_ppa_obligations     -> required: []      # its form fires reliably. This is why.
```

**Fix (both halves are needed — measured):**
- **(a) Make the params optional** (`zone: str = ""`), and return a `needs_input`
  elicitation envelope when they're missing. This kills the *inventing* half.
- **(b) TELL the agent to call the tool bare** ("call it with no arguments if you
  have nothing; never invent a value to fill the call"). (a) alone is NOT enough:
  a live run proved the model still won't call a knowingly-underspecified tool —
  it asks in prose instead. Put this in the TOOL'S DOCSTRING so it rides the
  declaration to every skill, not in one skill's prompt.

**Unit tests cannot catch this.** The tool genuinely DOES return a form when
called bare (19 tests proved it). The model simply never called it. Only a live
stream shows the difference.

### 2. The agent does not know what day it is unless you tell it

Nothing grounds the date by default, so every relative phrase ("now", "this
year", "last week") resolves against the model's training era. `wrap_with_today`
(composed into every agent's instruction, computed PER REQUEST — never at import,
or a long-lived container serves a stale "today") fixes it. A confidently wrong
year is worse than a refusal: it looks right.

### 3. `a2ui.enabled: false` stops the TOOL, not the model TYPING A2UI

The Model-B gate removes the A2UI toolset — it cannot stop a model printing a
v0.9 `createSurface` blob into chat as prose, which it did, on `pro`, with the
instruction "do NOT author any UI" right there in its prompt. Enforced at the
boundary instead: `make_authored_a2ui_stripper` (after_model, wired only for
a2ui-disabled skills) strips A2UI-shaped blobs and keeps the surrounding prose.
Protocols-first is architectural — do not trust model goodwill for it.

### 4. An empty run finishes SILENTLY

A turn that emits only reasoning ends as `RUN_FINISHED` with no text and no tool
call — ADK logs "The last event is partial, which is not expected" and the UI
just stops. It is not a `RUN_ERROR`, so nothing downstream can even tell it
failed. `stream_agui_events` now rewrites an empty `RUN_FINISHED` into a visible,
retryable `RUN_ERROR`. Note an **A2UI-surface-only run is NOT empty** — a
workbench render with no chat text is a real result (that exemption is load-bearing).

### 5. The elicitation registry gates on TOOL NAME

A tool that starts returning an elicitation envelope renders **nothing** until
the shared transform is registered for its name — the success transform declines
it, and a decline STOPS the search rather than falling through. One line:
`register_elicitation_for("my_tool")`. This is the recurring silent-render trap
wearing a different hat.

### 6. An unmapped result >50K is offloaded, and the agent reads the ID aloud

`_handle_large_output` dumps it to an artifact, and the agent then narrates
"the full dataset is available in the artifact `<tool>_response_e-…`" — a raw id
at a human (#9) and a dead end. Registering a mapping with `tool_names=[...]`
marks the tool offload-EXEMPT **and** gives it a tab. One fix, both problems.

### 7. `after_model_callback` sees PARTIAL responses — never edit chat text there

Text streams. The callback fires **once per streamed chunk**, and each chunk's
`content.parts` holds only that fragment — not the assembled reply. So any
callback that rewrites text is editing a random slice of a sentence, and the
fragment it cuts has ALREADY gone to the user as a `TEXT_MESSAGE_CONTENT` delta.

Measured 2026-08-11 while trying to strip a duplicated source list from a reply:
36 deltas in one turn; the stripper matched a trailing list *inside* chunk 33 and
the user got a mangled tail (`### 2026 European PPA Price Index Reports*`) mid-answer.

- **Removing** something from streamed text is not possible from here — the
  tokens are already sent. Prevent it upstream (prompt, or the tool result the
  model is copying from) instead.
- `make_authored_a2ui_stripper` carries the same exposure by construction: it is
  safe only while an authored A2UI blob happens to land inside one chunk.
- A callback that only *reads* (budget accounting, metrics) is fine.

### 8. Model tier is a correctness property, not a cost knob

`lite` is right for a FRONT DOOR (first-token latency, no chaining). It is not
enough for a specialist that must chain discover → open → extract → summarise:
measured, 1 of 2 identical runs emitted an EMPTY turn, and the run that fired
stopped after `list_documents` without ever calling the tool the task needed.
If a skill's core journey chains tools, it needs `pro`+.

**Acted on 2026-07-21:** the chaining specialists (one-ppa-expert,
one-obligation-analysis, one-doc-compare, document-analyst, one-knowledge-search,
web-researcher, code-assistant) are pinned to `pro`; only true front doors
(one-assistant, general-assistant) and mechanical schema-fill (data-extractor)
sit on `lite`. Thinking depth is now a separate per-skill knob
(`SkillMetadata.thinking`, see `config/thinking.py`) — front doors run `low`
for TTFT, specialists `dynamic`. Do not read "single tool = light skill":
one-knowledge-search declares one tool but does heavy synthesis, so it is `pro`.
The right question is what happens AFTER the tool returns, not how many tools
are declared. NOTE this ladder is validated on latency + this recorded finding,
NOT yet on a capability eval — a pass-rate eval over the real journeys is owed.

### Verification bar (why all of the above got shipped)

Every one of these passed unit tests. Three demo cards were advertised as
"verified" on isolated tool tests + jsdom and all three failed live, silently.
**Stream the exact user prompt against a deployed env N times and report a PASS
RATE.** The prices journey shipped at 10/10 (+ 5/5 zero-arg) — and that gate is
what caught #1(b), which no test could.
