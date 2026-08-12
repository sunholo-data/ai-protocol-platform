# Sprint Plan: BUDGET-ENFORCEMENT — v6.2.0 Sprint 2.12

## Summary

Ship the platform-level budget interface designed in
[budget-enforcement.md](budget-enforcement.md). Adds a pluggable
`BudgetEnforcer` Protocol, a reference in-memory impl, ADK
before/after model callbacks, and an AG-UI error path for hard blocks.

This is the second AIPLA template-extension after sprint 2.11
([anonymous-group-id-auth.md](anonymous-group-id-auth.md)),
and the first one with a hot-path enforcement gate. AIPLA's
fork-side `FirestoreBudgetEnforcer` plugs the same Protocol — no
platform changes needed for AIPLA's specific cohort/class schema.

**Duration:** 1.5 focused days + 0.3d buffer
**Scope:** Backend Protocol + ref impl + ADK callbacks + skill config + AG-UI error event + frontend banner + howto + smoke
**Dependencies:**
- [backend/observability/llm_metrics.py](../../../../backend/observability/llm_metrics.py) — existing `estimate_cost(model, input_tokens, output_tokens)` reused for pre-call cost projection
- [backend/adk/callbacks.py](../../../../backend/adk/callbacks.py) — extends the callback-factory pattern (existing `make_*` builders return composed callbacks)
- [backend/adk/agent.py](../../../../backend/adk/agent.py) — currently wires `before_model_callback=_document_injector` as a SINGLE callback; M2 introduces `_composed_before_model` mirroring the existing `_composed_before_agent` chain
- Pairs cleanly with shipped 2.11 ANON-GROUP-AUTH — `User.group_id` is the canonical AIPLA identity key

**Risk Level:** Medium — `EARNED_TRUST +1` and `GRACEFUL_DEGRADATION +1` are both reliant on soft/hard threshold semantics being right; tested explicitly at both layers.

**Design Doc:** [budget-enforcement.md](budget-enforcement.md) — Protocol shape, axiom alignment (+7 net), security considerations, performance budget (≤50ms per consult).

**Deadline:** AIPLA pre-pilot ≈ 2026-06-15 (mid-June). 1.5-day estimate; ~4 weeks of slack.

## Velocity Context (7-day rolling)

- 67 commits, ~25.8k insertions over 7 calendar days (covers sprints 2.9 + 2.10 + 2.10 follow-up + 2.11).
- Sprint 2.11 shipped same-shape work (Protocol + endpoint + reference impl + tests + smoke + howto + audit-table flip) at 2340 LOC actual vs 1490 estimated (~57% over) — the overshoot was entirely in the 7-gate test matrix at two layers.
- Sprint 2.12's gate matrix is smaller (allow / warn / block / exempt / multiplier / period-rollover / multi-identity-isolation / replay-dedup = 8 cases at the function layer, ~6 at the integration layer) — apply the same lesson and budget ~1400–1600 actual LOC vs the ~1000 raw estimate.
- Code is mostly mechanical once the Protocol is shaped. **Keep the 1.5-day TIME estimate; expect actual LOC to land ~50% above the raw sum below.**

## Milestone Breakdown

The design doc's Implementation Plan defines four phases. Translating verbatim into milestones with concrete acceptance criteria:

### M1 — Protocol + reference impl + unit tests (`backend`, ~0.4d)

**Files**
- `backend/budget/__init__.py` (~10 LOC) — package init + public re-exports
- `backend/budget/enforcer.py` (~120 LOC) — `BudgetConsultation` + `BudgetDecision` frozen dataclasses, `BudgetEnforcer` Protocol (with `@runtime_checkable`), `register_budget_enforcer()` + `get_registered_enforcer()` registry, `BudgetExceededError` exception (carries decision payload for AG-UI translation later)
- `backend/budget/in_memory_enforcer.py` (~180 LOC) — ref impl with dict-keyed cost tracking (`{(identity_value, period_key): float}`), env-driven caps (`BUDGET_DEFAULT_CAP_USD`, `BUDGET_SOFT_THRESHOLD`=0.8, `BUDGET_PERIOD`=`monthly`), asyncio lock around consult+record, invocation_id dedup (60s rolling window via `(invocation_id, timestamp)` tuple set)
- `backend/tests/unit/test_budget_enforcer.py` (~250 LOC) — Protocol conformance, period rollover, multi-identity isolation, replay dedup, exempt-cap bypass
- `backend/tests/unit/test_in_memory_enforcer.py` (~150 LOC) — gate matrix (8 cases below)

**Acceptance criteria**
1. `BudgetEnforcer` is a `runtime_checkable` `typing.Protocol` — `isinstance(InMemoryBudgetEnforcer(), BudgetEnforcer)` returns `True`.
2. `consult` + `record` are both `async` — fork impls can do I/O.
3. **Gate 1 — allow under cap:** consult with `projected_cost_usd` < `cap * soft_threshold` → `BudgetDecision(action="allow", remaining_usd=..., period_end=..., message=None)`.
4. **Gate 2 — warn above 80% soft threshold:** consult that pushes cumulative cost ≥ `cap * 0.8` and < `cap` → `action="warn"` with non-empty `message`.
5. **Gate 3 — hard block above 100%:** consult that pushes cumulative cost ≥ `cap` → `action="block"` with `retry_after_seconds` set to seconds-until-period-end.
6. **Gate 4 — period rollover resets state:** monkeypatch the period key to a future window; `remaining_usd` resets to the full cap.
7. **Gate 5 — multi-identity isolation:** spending on identity A does not affect identity B's remaining.
8. **Gate 6 — replay dedup via invocation_id:** the same `(invocation_id, identity_value)` consulted twice within 60s does not double-charge; second call returns the cached decision.
9. **Gate 7 — record updates state:** after `record(consultation, actual_cost_usd)`, subsequent `consult` reflects the realised cost (not the projection).
10. **Gate 8 — fail-loud on missing config:** if `BUDGET_DEFAULT_CAP_USD` is unset AND no per-identity cap is registered, `consult` returns `action="allow"` (default-deny is opt-in via env). Logged at WARN level.

**Risk:** Period-rollover testing requires a `time_provider` injection point — apply the same CLASS-attribute pattern from sprint 2.11's `AnonymousGroupAuth.time_provider` (set after `@dataclass` decoration so tests can override via `InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t)`).

### M2 — ADK callback + skill config + agent.py wiring (`backend`, ~0.5d)

**Files**
- `backend/budget/callback.py` (~140 LOC) — `make_budget_callback(enforcer)` returns a `before_model_callback`; `make_budget_record_callback(enforcer)` returns an `after_model_callback`. Identity extraction reads `User.<identity_key>` per `BudgetConfig.identity_key`. Cost estimation reuses [`llm_metrics.estimate_cost`](../../../../backend/observability/llm_metrics.py) with worst-case `max_output_tokens` (read from `llm_request.config.max_output_tokens`, default 4096).
- `backend/adk/budget_config.py` (~70 LOC) — `BudgetConfig(BaseModel)` Pydantic model: `identity_key: str`, `cost_multiplier: float = 1.0`, `exempt: bool = False`. Mirrors `A2uiToolConfig.from_tool_configs(...)` exactly ([backend/adk/a2ui.py:149](../../../../backend/adk/a2ui.py)).
- `backend/adk/agent.py` — introduce `_composed_before_model(callback_context, llm_request)` async helper that chains `_document_injector` + `_budget_gate`; introduce `_composed_after_model(callback_context, llm_response)` for the record callback. Replace the current single-callback wiring at L422.
- `backend/tests/integration/test_budget_callback.py` (~200 LOC) — end-to-end with in-memory enforcer + stub LLM

**Acceptance criteria**
1. `BudgetConfig.from_tool_configs(md.tool_configs)` parses `tool_configs.budget` into a typed model; missing or empty → returns `None` (skill is exempt by absence).
2. `cost_multiplier=3.0` declared on a skill scales `projected_cost_usd` by 3 BEFORE the enforcer is consulted.
3. `exempt=true` declared on a skill bypasses `consult` entirely — no enforcer call, no log line.
4. **Allow path:** consult returns `action="allow"` → model call proceeds normally. Verified by stub LLM emitting a response.
5. **Warn path:** consult returns `action="warn"` → model call proceeds AND `callback_context.state["budget:warn_message"]` is set to `decision.message`. Cleared after the after_agent_callback reads it.
6. **Block path:** consult returns `action="block"` → `BudgetExceededError` is raised BEFORE the model is invoked. Stub LLM's `generate_content` is never called (asserted via mock call_count == 0).
7. `make_budget_record_callback`: after a successful model call, `enforcer.record(consultation, actual_cost_usd)` is called with the realised cost from `llm_response.usage_metadata` (input + output tokens × pricing table). `record` is NOT called when `action == "block"` (no model call happened).
8. **Back-compat:** existing skills without `tool_configs.budget` continue to work identically; the existing `_document_injector` still runs as the first link in the composed chain.
9. **No enforcer registered:** if `get_registered_enforcer()` returns `None`, the budget callback is a no-op (returns `None` immediately, no log spam).
10. **Identity extraction edge cases:** `identity_key="group_id"` reads `user.group_id`; if the field is empty string OR missing, the consult is skipped with a structured log line at WARN level (`identity_unresolved`). Fails open by design — security note in the design doc.

**Risk:** `agent.py` currently has `before_model_callback=_document_injector` as a direct wire, NOT a composed chain. This milestone introduces the composition. The change is intrusive but mechanical — the existing `_composed_before_agent` (L345) is the template. Verify no regression by running the full existing `tests/integration/` suite that uses the document injector.

### M3 — AG-UI error translation + frontend banner + warn-prefix injection (`fullstack`, ~0.3d)

**Files**
- `backend/protocols/agui/` (or wherever the AG-UI translator lives) — catch `BudgetExceededError` raised from M2's before_model_callback, emit `RUN_ERROR` AG-UI event with `kind="budget_exceeded"`, `message=<decision.message>`, `retry_after_seconds=<decision.retry_after_seconds>` (~40 LOC patch)
- `backend/adk/callbacks.py` — add warn-prefix injection inside the existing `after_agent_callback` chain: if `state["budget:warn_message"]` is set, prepend a structured prefix to the final assistant text and clear the state key (~25 LOC patch)
- `frontend/src/components/budget/BudgetBanner.tsx` (~90 LOC) — renders message + retry-after countdown (live-decrementing); dismisses on subsequent success; controlled by `useSkillAgent` exposed banner state
- `frontend/src/hooks/useSkillAgent.ts` (~40 LOC patch) — classifier branch on the AG-UI `RUN_ERROR.kind === "budget_exceeded"` event; sets `budgetBanner` state; suppresses the generic "Something went wrong" fallback so the banner is the user-facing surface
- `frontend/src/components/budget/__tests__/BudgetBanner.test.tsx` (~80 LOC) — banner renders message + retry-after; countdown decrements; dismisses on `retry` prop change
- `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` (~50 LOC additions) — classifier sets banner state on `kind=budget_exceeded`; banner clears on next successful run

**Acceptance criteria**
1. Backend: `BudgetExceededError` raised from any callback in the chain is intercepted by the AG-UI translator (NOT bubbled up as a generic 500); emitted as a typed `RUN_ERROR` event with `kind`, `message`, `retry_after_seconds`.
2. Backend: warn-prefix injection only fires when `state["budget:warn_message"]` is set; the state key is cleared after the injection (no leak across turns).
3. Frontend: `useSkillAgent.onRunFailed` classifies events with `kind === "budget_exceeded"`; the rest of the error-classifier branches (`run_error`, `tool_error`) are untouched.
4. Frontend: `BudgetBanner` renders the backend message verbatim (no client-side interpolation) and a live retry-after countdown; banner auto-dismisses 1s after the countdown hits 0.
5. Frontend: a subsequent successful run (next `RUN_FINISHED`) clears the banner state, even if the countdown hasn't expired.
6. Frontend: existing `useSkillAgent` error tests (`kind=run_error`, `kind=tool_error`) still pass — banner state is independent of the existing error surface.

**Risk:** Warn-prefix injection mutates the assistant turn. Must NOT fire when the user explicitly opted into a different rendering surface (e.g. workspace-only skill where the chat bubble would be incorrect). Mitigation: prefix injection is gated on the chat surface being active — if not, the warning is dropped and a structured log line records the skip.

### M4 — Howto + smoke + audit-table + implemented/ move (`fullstack`, ~0.3d)

**Files**
- `docs/integrations/budget-enforcement.md` (~250 LOC) — fork adoption howto: registering a custom `BudgetEnforcer`, env-var configuration knobs (`BUDGET_DEFAULT_CAP_USD`, `BUDGET_SOFT_THRESHOLD`, `BUDGET_PERIOD`), identity scheme guidance, no-PII recommendation, multi-instance scale-out caveat (in-memory ref impl is single-instance only — multi-instance Cloud Run needs an external store), AIPLA `FirestoreBudgetEnforcer` reference sketch as appendix
- `docs/talks/ai-ui-protocol-stack.md` — flip the sprint 2.12 audit-row from 📋 → ✅ with the verification-log entry pattern from sprint 2.11
- `docs/design/v6.2.0/SEQUENCE.md` — row 2.12 marked shipped; doc link → `implemented/`
- `docs/design/v6.2.0/budget-enforcement.md` + `docs/design/v6.2.0/budget-enforcement-sprint.md` → moved to `implemented/`; relative-path depth fixed (`../../../` → `../../../../` for backend refs)
- `.dev-logs/budget-enforcement-smoke.png` — chrome-devtools MCP screenshot of working flow

**Acceptance criteria**
1. **Smoke A (warn at 80%):** set `BUDGET_DEFAULT_CAP_USD=0.01` and configure a skill with `tool_configs.budget.identity_key="uid"`; fire successive turns until cumulative cost crosses 80% — observe the warn-prefix in the chat response.
2. **Smoke B (hard block at 100%):** continue from Smoke A — fire another turn that crosses 100% — observe the `BudgetBanner` with retry-after countdown; the model is NOT invoked (verify via dev-logs no LLM call line).
3. **Smoke C (recovery via period rollover):** monkeypatch the period key to a future window (or set a `BUDGET_PERIOD=daily` env + advance system clock); fire a turn — observe the call succeeds and the banner clears.
4. Howto covers: enforcer registration, config knobs, identity scheme guidance, no-PII contract, multi-instance scale-out caveat, AIPLA reference sketch.
5. Talk-doc audit row updated with the verification-log entry pattern from sprint 2.11's row.
6. Both design docs moved to `implemented/`; SEQUENCE.md row 2.12 marked shipped with the correct date.

**Risk:** Smoke C requires a `time_provider`-injectable period key — keep the M1 time-injection pattern in mind. If chrome-devtools is flaky, fall back to a curl-based smoke documented as a back-up verification path.

## Day-by-Day (1.5-day plan)

| Day | Morning | Afternoon |
|---|---|---|
| 1 | M1: Protocol + types + ref impl + 8-gate unit tests; close M1 | M2: callback + BudgetConfig + agent.py composition; integration tests; close M2 |
| 2 (half) | M3: AG-UI error translation + BudgetBanner + useSkillAgent classifier + warn-prefix; tests; close M3 | M4: howto + smoke + audit-table + implemented/ move; close sprint |
| (slack) | Buffer for unknowns; AIPLA reference-sketch refinement | Buffer |

The 1.5-day budget assumes no architectural surprises. **The biggest unknown is M2's composition refactor** — if `_composed_before_model` introduction surfaces hidden coupling in existing tests, pause and reassess. The existing `_composed_before_agent` (L345) shows the pattern is well-trodden, so this is unlikely.

## Quality Gates (per milestone close)

```bash
# Backend (M1 + M2)
cd backend && make lint && make test-fast

# Frontend (M3)
cd frontend && npm run quality:check

# Full pre-push parity (M4)
cd backend && make lint && make test-fast
cd frontend && npm run quality:check
```

## Push Policy

- Commit at each milestone close (4 commits expected).
- DO NOT push until user confirms — per project pre-push review convention.
- Final smoke verification via chrome-devtools MCP before requesting push.

## Open Questions (carried from design doc)

1. **Should the warn message be model-rendered or platform-rendered?** Design doc recommends platform-rendered (deterministic prefix) for v1 — keep that decision; revisit in v2 if user feedback wants nicer phrasing.
2. **Per-model budget caps?** Trivially supportable by `identity_value = "group:X|model:gpt-5"` convention; document in M4 howto but don't ship a separate API.
3. **Daily soft-reset of "warned today" flag?** Punt to ref impl decision — the in-memory impl re-warns on every call above 80% (annoying but correct); forks can dedup.

Resolution: none of these blocks the AIPLA mid-June pre-pilot.

## Out of Scope (explicitly)

- **Billing integration / payment gateway** — platform tracks and gates; charge-the-customer is downstream.
- **Token-stream-level gating** — gate is per-call, not per-token. Mid-stream cancellation has UX nuance left to forks.
- **Forecast-based gating** — "you'll hit your limit by Friday" is out of scope for v1.
- **Specific per-tenant identity scheme** — the Protocol takes an arbitrary `identity_value: str`; AIPLA's group/class is a fork-side detail.
