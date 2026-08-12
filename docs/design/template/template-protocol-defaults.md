# Template Protocol-Stack Defaults

**Status**: Partially implemented in platform (2026-06-05) — **template sync pending**. G24 + G26 shipped; G25 documented in gotchas + SKILL.md prose; G27 confirmed already v0.9-correct in existing starter skills. Deferred for follow-up: deploy-time A2UI schema validator (G27 part 2), starter `_starter/v1` artefact with vanilla-JS JSON-RPC helpers (lives with mcp-apps-artefacts work).
**Priority**: P1 (highest signal: the function-as-schema flip removes a 3-5s/specialist Gemini round-trip)
**Estimated**: 3d (~1d already shipped in platform; ~2d remains as documented follow-ups)
**Scope**: Backend (tools/, adk/, skills/templates/) + Frontend (JsonCardBuilder)
**Dependencies**: None
**Created**: 2026-06-05
**Last Updated**: 2026-06-05
**Source items**: G24 (function-as-schema default), G25 (A2UI updateDataModel per-path), G26 (compose_after_agent_callbacks helper), G27 (A2UI v0.9 Button shape + deploy-time schema validator) — captured by gde-ap-agent fork 2026-06-03 during AP demo polish

## Implementation Status (2026-06-05)

| Item | Status in platform repo | Files |
|------|------------------------|-------|
| G24 short-circuit | ✅ Shipped | `tools/structured_extraction.py` — `_agent_already_emitted_typed_payload()` + short-circuit at top of `structured_extraction_callback`. Tests: `tests/tool_tests/test_structured_extraction.py::TestFunctionAsSchemaShortCircuit` (4 cases). |
| G24 `emit_*` exemplar | Deferred (fork-specific) | Canonical `emit_<schema>` tools live in fork code (e.g. `gde-ap-agent/backend/tools/ap_pipeline_emit.py`). The template documents the pattern but does not ship example emit tools. |
| G25 per-path patches | ✅ Documented | `docs/ops/gotchas.md` — "A2UI updateDataModel without `path` defaults to root-REPLACE (G25)" with multi-stage vs single-stage authoring rule. |
| G26 callback composition | ✅ Shipped | `adk/callbacks.py::compose_after_agent_callbacks` + wired in `adk/agent.py::create_agent` (replaces the bespoke `_composed_after_agent` that dropped returns). Tests: `tests/unit/test_compose_after_agent_callbacks.py` (5 cases). |
| G27 v0.9 Button shape | ✅ Already correct | `backend/skills/templates/workspace-demo-interactive/SKILL.md` already documents and prescribes the v0.9 form (child Text id + `action.event`). No code changes needed; deploy-time validator is a follow-up. |

**What's next** for full design-doc closure: deploy-time A2UI schema validator script (G27 part 2; nice-to-have, catches v0.8 regressions at build time rather than runtime); ship a canonical `_starter/v1` artefact with vanilla-JS JSON-RPC helpers (more naturally lives in [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) implementation work).

## Problem Statement

The protocol stack (AG-UI / A2UI / MCP / function-calling) is doing useful work in
downstream forks. The friction comes from **template-layer defaults** — the patterns the
template seeds into starter SKILL.md files, the composition wrappers in `backend/adk/`,
and the JSON snippets in skill examples — that push fork authors into subtly wrong
shapes. None of the four items below is a protocol bug; each is a default that the
template chose poorly and now bites every multi-stage fork.

**Current State:**

- **G24** `backend/tools/structured_extraction.py` + `backend/tools/schemas/__init__.py` are the template's primary path for schema-validated JSON output. They fire a **second Gemini call** with `response_mime_type: application/json` + `response_schema: <schema>` after the agent runs, adding +3–5s latency per specialist. The pattern was chosen for determinism in [docs/design/forks/gde-ap-agent/schema-enforced-extraction.md](../forks/gde-ap-agent/schema-enforced-extraction.md) — "the callback fires unconditionally; no LLM discretion."
- **G25** Starter SKILL.md JSON shows `updateDataModel` with `value: { ... }` and no `path` field. The A2UI v0.9 SDK defaults missing `path` to `"/"`, then calls `surface.dataModel.set("/", value)` — **replacing the entire data model**. Every multi-stage skill (extractor → validator → poster) clobbers prior fields on each step; only the last specialist's payload survives on screen.
- **G26** `backend/adk/agent.py` `_composed_after_agent(callback_context) -> None` composes after-agent callbacks but its return type is annotated `None` and **silently drops** the return value of each callback. ADK only emits an extra response event when an after-agent callback returns `Content`; with `None`, the schema-validated JSON Part never reaches the wire and `JsonCardBuilder` has nothing to render. Twelve-line bug; hours to diagnose because every component looks right in isolation.
- **G27** Starter SKILL.md JSON snippets ship the **v0.8 `Button` shape** (`{component: "Button", label, action: "stringName", context: {}}`). A2UI v0.9's BasicCatalog requires `{component: "Button", child: "<text-id>", action: {event: {name, context}}}` plus a sibling `Text` component for the child. The LLM hits a validation error on attempt 1 and self-corrects to v0.9 on attempt 2 — a wasted Gemini turn per pipeline run.

**Impact:**

- Forks pay 3–5s/specialist latency every chat turn — a three-stage AP pipeline shows a measurable, visible delay vs. the function-calling path (G24).
- Multi-stage workspace surfaces appear "broken" to users: open the validator's card, see the validator's fields, lose the extractor's vendor/amount silently (G25).
- Forks that compose callbacks (the recommended pattern) get silently broken structured-output cards — debugging looks at the protocol, the frontend, the prompts, before the template's wrapper itself (G26).
- Every pipeline run pays one wasted Gemini turn at the workspace-card emit step (G27).

**What works (do not touch):** A2UI workspace surface mount, AG-UI streaming, `SequentialAgent` orchestration, MCP for vendor-master/erp-posting. The frictions are template-layer defaults around these protocols, not protocol bugs. See [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) for the broader "protocols good, defaults need work" framing.

## Goals

**Primary Goal:** The template's structured-output, multi-surface-update, callback-composition, and A2UI-component patterns should be **fast, correct, and protocol-current by default** — fork authors get the right behaviour without having to discover and override the seed.

**Success Metrics:**
- First-token-to-completed-emit latency for a single specialist drops by ≥3s (function-calling replaces response_schema second-call as the default).
- A three-stage pipeline (extractor → validator → poster) renders **all** payload fields on the final workspace card, with no silent root-replace.
- Composed after-agent callbacks that return `Content` reliably surface that `Content` as an AG-UI event.
- Zero v0.8/v0.9 `Button` validation errors in a fresh fork's first pipeline run (the starter JSON is v0.9-current AND a deploy-time validator gates regressions).

**Non-Goals:**
- Removing the `response_schema` callback. It remains as the fallback for the LLM-forgot-to-call-the-tool case; only the primary path changes.
- Auto-migrating existing fork SKILL.md files. The flip is a template default; per-fork overrides (including [`forks/gde-ap-agent/schema-enforced-extraction.md`](../forks/gde-ap-agent/schema-enforced-extraction.md)) stay valid.
- A general-purpose A2UI starter library. This doc scopes the four specific defaults that bit the AP demo; broader A2UI ergonomics belong with [G22 follow-up `template-chat-surface-defaults.md`](./template-chat-surface-defaults.md).

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | **+2** | Function-as-schema removes one full Gemini round-trip per specialist — 3–5s saved per stage; a three-stage pipeline saves ~10s of perceived latency on the critical path |
| 2 | EARNED TRUST | +1 | Audit pane shows the **real** emitted payload, not the STOP-after-emit sentinel; root-replace bug fixed means users see what the agent extracted |
| 3 | SKILLS, NOT FEATURES | +1 | The template ships better skill-author defaults; new skills inherit fast + correct patterns automatically |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Function-calling is the model-native structured-output path — leverages Gemini's typed function-calling instead of routing through a second constrained-decoding call |
| 5 | GRACEFUL DEGRADATION | +1 | `response_schema` callback remains as fallback for "LLM forgot to call the emit tool"; failure mode is bounded |
| 6 | PROTOCOL OVER CUSTOM | +1 | A2UI per-path patches use the protocol primitive correctly (JSON Pointer paths); v0.9 Button shape is spec-current |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | +1 | Deploy-time A2UI schema validator surfaces v0.8/v0.9 regressions at build time, not in production |
| 9 | SECURE BY CONSTRUCTION | 0 | No security surface change |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Both clients and protocol surface unchanged |
| | **Net Score** | **+8** | Strong alignment — proceed |

**Conflict Justifications:**
- None (no -1 scores).

## Design

### Overview

Flip four template defaults: (G24) make function-as-schema the primary path for structured output with `response_schema` callback as fallback; (G25) ship `updateDataModel` with explicit per-path patches in starter SKILL.md JSON; (G26) ship a `compose_after_agent_callbacks` helper analogous to `compose_before_tool_callbacks` that forwards the first non-None `Content` return; (G27) update all starter A2UI JSON to the v0.9 `Button` shape and gate regressions with a seed-time schema validator.

### G24 — Function-as-schema as the primary structured-output path

**Files:** `backend/tools/<skill>_emit.py` (new pattern), `backend/tools/structured_extraction.py` (short-circuit), `backend/skills/templates/*/SKILL.md` (frontmatter + prompt update)

Each specialist that needs schema-validated output ships an `emit_<schema>` `FunctionTool` whose **typed parameters mirror the JSON Schema**. Gemini's function-calling enforces the types as schema — the model literally cannot emit a call with the wrong arg types.

```python
# backend/tools/ap_pipeline_emit.py (canonical example)
async def emit_invoice_extraction(
    vendor_name: str,
    invoice_number: str,
    currency: str,
    total: float,
    # … optional typed fields …
    tool_context: ToolContext = None,
) -> str:
    """Emit the extracted invoice in the canonical ap_invoice schema.

    Call exactly once at end of turn. The typed parameters ARE the
    schema; Gemini's function-calling enforces them.
    """
    payload = {"vendor_name": vendor_name, "invoice_number": invoice_number, ...}
    tool_context.state["app:emitted:invoice"] = payload
    return "Emitted ap_invoice. STOP. End turn now."
```

**SKILL.md changes:**
- Add `emit_invoice_extraction` (and siblings) to `tools:`.
- Prompt body: *"Call `emit_invoice_extraction` exactly once at the end of your turn with the extracted fields."*

**`structured_extraction.py` short-circuit (G24 fallback):**

```python
# backend/tools/structured_extraction.py — at top of structured_extraction_callback
for key in (
    "app:emitted:invoice",
    "app:emitted:verdict",
    "app:emitted:posting",
):
    if callback_context.state.get(key):
        return None  # function-as-schema win — skip the second Gemini call
```

The callback runs only when the LLM forgot to call the emit tool, preserving the
determinism guarantee from [forks/gde-ap-agent/schema-enforced-extraction.md](../forks/gde-ap-agent/schema-enforced-extraction.md)
for the failure case while keeping the success path fast.

**Trade-offs (must appear in template docs):**

| Aspect | response_schema callback | function-as-schema (new default) |
|---|---|---|
| LLM calls per specialist | 2 | 1 |
| Latency overhead | +3–5s/specialist | none |
| Schema enforcement | constrained decoding | function-calling types |
| Failure mode | callback always runs | LLM may forget to call tool → fallback fires |
| Frontend integration | text Part needs JSON sniffing | tool-call event is canonical |
| Determinism story | strong (unconditional) | strong w/ fallback safety net |

**Migration note:** [docs/design/forks/gde-ap-agent/schema-enforced-extraction.md](../forks/gde-ap-agent/schema-enforced-extraction.md) explicitly chose `response_schema` callback for determinism. That decision remains valid for that fork; this doc only flips the **template default** so new forks inherit the faster path. Update that fork's doc with a one-line note that the template default has flipped — the fork is free to keep its callback-primary stance.

### G25 — A2UI `updateDataModel` per-path patches as the default

**Files:** `backend/skills/templates/*/SKILL.md`, `backend/tools/a2ui_helpers.py` (if it exists), `docs/ops/a2ui-guide.md`

A2UI v0.9 spec: `updateDataModel.value` with no `path` defaults to `"/"` and the SDK
calls `surface.dataModel.set("/", value)` — **root replace**. The fix is at the
authoring layer: starter SKILL.md JSON and the workspace-card examples ship per-path
patches.

```json
[
  { "version": "v0.9", "updateDataModel": { "surfaceId": "workspace", "path": "/status", "value": "VALID" } },
  { "version": "v0.9", "updateDataModel": { "surfaceId": "workspace", "path": "/verdict", "value": "matches vendor master" } }
]
```

Each `set("/status", "...")` patches a single leaf, leaving prior fields intact.

**A2UI docs section (`docs/ops/a2ui-guide.md` — new or extended):**
> **Root-replace gotcha.** Omitting `path` defaults to `"/"`, which replaces the entire
> data model on the named surface. Multi-stage skills should emit one `updateDataModel`
> per field, with explicit JSON Pointer paths (`/status`, `/verdict`, `/glCode`). Root
> replace is correct only when an earlier stage's data is no longer relevant.

Add a 30-second worked example to the workshop talking points (see
[docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)) — show the
clobber bug, then the per-path fix.

### G26 — `compose_after_agent_callbacks` helper

**File:** `backend/adk/callbacks.py` (or wherever `compose_before_tool_callbacks` lives), `backend/adk/agent.py` (use the helper)

Today `_composed_after_agent` is bespoke and silently drops returns. Ship a typed
helper analogous to `compose_before_tool_callbacks`:

```python
# backend/adk/callbacks.py
import asyncio
from typing import Awaitable, Callable, Optional
from google.genai.types import Content

AfterAgentCallback = Callable[..., Awaitable[Optional[Content]] | Optional[Content]]

def compose_after_agent_callbacks(*callbacks: AfterAgentCallback):
    """Compose after-agent callbacks; the first non-None return wins.

    ADK semantics: after-agent callbacks either mutate state and return
    None, OR return a follow-up Content event. Composition must forward
    the first non-None Content so a downstream component (e.g. structured
    extraction) can append a JSON Part the frontend renders as a Card.
    """
    async def composed(ctx):
        for cb in callbacks:
            result = await cb(ctx) if asyncio.iscoroutinefunction(cb) else cb(ctx)
            if result is not None:
                return result
        return None
    return composed
```

**Wire-up in `backend/adk/agent.py`:**

```python
# Before
async def _composed_after_agent(callback_context: object) -> None:
    _after_agent_response(callback_context)
    await structured_extraction_callback(callback_context)  # return dropped

# After
from backend.adk.callbacks import compose_after_agent_callbacks

after_agent_callback = compose_after_agent_callbacks(
    _after_agent_response,
    structured_extraction_callback,
)
```

The helper is generic — any skill that composes callbacks gets the same
return-value-forwarding behaviour.

### G27 — A2UI v0.9 Button shape + deploy-time schema validator

**Files:** `backend/skills/templates/*/SKILL.md` (JSON snippets), `backend/scripts/validate_a2ui_surfaces.py` (new), `cloudbuild.yaml` (validator step)

**Authoring change:** every starter SKILL.md A2UI JSON snippet uses the v0.9 Button shape:

```json
{ "id": "btn", "component": "Button", "child": "btn_text",
  "action": { "event": { "name": "approve_invoice", "context": { "invoiceId": "{{ /invoiceNumber }}" } } } }
{ "id": "btn_text", "component": "Text", "text": "Approve" }
```

**Validator script (`backend/scripts/validate_a2ui_surfaces.py`):**

```python
# Loads every SKILL.md A2UI surface JSON example, runs it through the
# @a2ui/web_core schema validator (via a small Node bridge or by porting
# the v0.9 schema to a Python validator), exits non-zero on any error.
# Run at seed time AND in CI.
```

**Cloud Build step (`cloudbuild.yaml` excerpt):**

```yaml
- name: 'gcr.io/cloud-builders/uv'  # or whichever runner
  id: 'validate-a2ui-surfaces'
  entrypoint: 'bash'
  args: ['-c', 'cd backend && uv run python scripts/validate_a2ui_surfaces.py']
```

A v0.8 → v0.9 regression now fails the build, not the runtime. Pair with the existing
`tests/integration/` pattern.

### Workshop alignment

The companion workshop ([docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)) should teach the new defaults up front, with the old patterns appearing only as worked examples of "the friction we removed." Five talking points to add:

1. **Function-as-schema is the default, not the response_schema callback.** Save the callback for the case where the specialist genuinely has no tools.
2. **A2UI per-path patches.** Demo the root-replace clobber in 30 seconds; show the per-path fix.
3. **`after_agent_callback` return values are the canonical follow-up-event channel.** Show the composed-callbacks helper.
4. **Ship verifying smoke scripts.** A `whoami_smoke.py` + `verify-judge-path.sh` pair lets an agent self-verify a deploy. (Coordinates with [template-fork-ergonomics.md G23](./template-fork-ergonomics.md#item-g23-—-sanitize-pipeline-strips-whoami-smoke-code-but-leaves-the-docs).)
5. **Don't conflate protocol limitations with template-default limitations.** The protocol stack here is solid; template ergonomics are what needs work. Critical for the workshop's pitch.

### CLI Surface

No new commands. The `aiplatform skills seed` (or equivalent) command picks up the
schema-validator step via the same CI/cloudbuild path; the validator can also be
invoked locally with `cd backend && uv run python scripts/validate_a2ui_surfaces.py`.
Consider a future `aiplatform a2ui validate <skill>` if a per-skill check becomes
common — out of scope for this sprint.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Author canonical `emit_<schema>` FunctionTools for the three AP specialists in `backend/tools/ap_pipeline_emit.py`; update their SKILL.md `tools:` and prompts (G24) | 4h |
| 2 | `structured_extraction.py` short-circuit on `app:emitted:<skill>` state key (G24 fallback) | 1h |
| 3 | Update starter SKILL.md JSON snippets + `docs/ops/a2ui-guide.md` with per-path `updateDataModel` pattern + 30s worked example (G25) | 3h |
| 4 | Add `compose_after_agent_callbacks` helper to `backend/adk/callbacks.py`; rewire `backend/adk/agent.py`; tests covering "first non-None return wins" + mixed sync/async (G26) | 3h |
| 5 | Update every starter SKILL.md A2UI JSON snippet to v0.9 Button + Text-child shape (G27 authoring) | 2h |
| 6 | Write `backend/scripts/validate_a2ui_surfaces.py`; add CI/cloudbuild step; document failure modes (G27 validator) | 3h |
| 7 | Update [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) with five new talking points + the friction → fix worked examples | 2h |
| 8 | Tests (pytest + a2ui-validator self-check + agent.py callback-composition regression) | 3h |
| 9 | Docs: README pointers, `cli/README.md` if applicable, `CLAUDE.md` mention of function-as-schema as default; one-line migration note in `forks/gde-ap-agent/schema-enforced-extraction.md` | 2h |

**Total: ~23h ≈ 3d** (matches sprint estimate; no PR-review buffer included).

## Testing Strategy

- **`test_ap_pipeline_emit.py`** — invoke each `emit_*` tool with typed args; assert state key is set and return string contains STOP marker.
- **`test_structured_extraction_shortcircuit.py`** — set `app:emitted:invoice` in callback context; assert `structured_extraction_callback` returns `None` without making a Gemini call (mock the model).
- **`test_compose_after_agent_callbacks.py`** — given three callbacks returning `[None, Content("A"), Content("B")]`, assert composed callback returns `Content("A")` (first non-None wins). Also test all-None and mixed-sync-async.
- **`test_validate_a2ui_surfaces.py`** — assert validator passes on a known-good v0.9 surface; fails (non-zero exit) on a deliberately-malformed v0.8 Button shape.
- **`test_a2ui_per_path_render.tsx`** — frontend integration test: drive `updateDataModel` with two per-path patches; assert both fields survive on screen.
- **CI**: add `validate_a2ui_surfaces.py` to `make lint` or a new `make validate-surfaces` target; gate on it in `.github/workflows/ci.yml`.
- **Manual smoke**: run a three-stage pipeline (extractor → validator → poster) against a fresh fork; assert (a) total latency drops by ≥6s vs. callback-only baseline, (b) workspace card shows all fields from all stages, (c) audit pane shows real emitted payload (paired with [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) Friction 7).

## Success Criteria

- [ ] Three-stage AP pipeline runs end-to-end with `emit_*` FunctionTools; no `response_schema` second Gemini call fires when the LLM correctly emits the tool (G24).
- [ ] When the LLM forgets to call `emit_*`, the `structured_extraction_callback` runs and produces the schema-validated JSON Part (G24 fallback).
- [ ] First-token-to-completed-emit latency for a single specialist drops by ≥3s vs. the response_schema-callback baseline (G24 KPI).
- [ ] After a three-stage pipeline run, the workspace surface card shows fields from **all three** specialists, with no silent root-replace (G25).
- [ ] `compose_after_agent_callbacks` ships in `backend/adk/callbacks.py`; `backend/adk/agent.py` uses it; a callback that returns `Content` reliably surfaces as an AG-UI event (G26).
- [ ] All starter SKILL.md A2UI JSON snippets pass `validate_a2ui_surfaces.py` with v0.9 schema; v0.8 Button shape is removed from every template file (G27).
- [ ] Cloud Build fails the deploy on any A2UI v0.8/v0.9 regression introduced in a starter surface JSON (G27 validator).
- [ ] [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) includes the five new talking points and worked examples.
- [ ] [forks/gde-ap-agent/schema-enforced-extraction.md](../forks/gde-ap-agent/schema-enforced-extraction.md) has a "Template-default-flip note" pointing here.
- [ ] All existing tests pass.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — items G24–G27 registered here
- [template-fork-ergonomics.md](./template-fork-ergonomics.md) — G19 sub-agent wiring + G21 `role` discriminator pair with G24's function-as-schema flow (a specialist invoked via structured-input endpoint emits via `emit_*` tool)
- [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) — pairs with this doc; Friction 7 (audit pane shows STOP sentinel) is fixed at the frontend layer once G24's emit pattern is the source of truth
- [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) — callback-composition patterns; the `_BLOCK_TEMPLATE` framing in `iframe_context.py` is the InstructionProvider analogue of the function-as-schema "tell the model exactly what to do" principle
- [forks/gde-ap-agent/schema-enforced-extraction.md](../forks/gde-ap-agent/schema-enforced-extraction.md) — original determinism-first decision; superseded **as template default** by this doc; per-fork override still valid
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — workshop tracker; absorbs G24–G27 as worked examples
