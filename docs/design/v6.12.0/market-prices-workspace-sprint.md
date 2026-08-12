# Sprint Plan — Market Prices Workspace (PRICES-WORKSPACE)

**Design doc**: [market-prices-workspace.md](market-prices-workspace.md)
**Sprint ID**: `PRICES-WORKSPACE`
**Estimated**: ~2.5d
**Created**: 2026-07-17
**Status**: In progress

## Sprint Summary

**Goal:** a BigQuery price query lands as an **explorable Workspace tab** (chart + sortable table + CSV + citation) instead of three scalars and an artifact id — and asking for prices with no params raises the form on the **first** turn.

**Foundation already shipped** (do not redo): `entsoe_day_ahead_prices` works live; its result→A2UI mapping is registered (`a2ui_entsoe_render.py`, `e108b89`) making it offload-exempt with its own tab; the full series is already in `updateDataModel`; `wrap_with_today` grounds the date.

**Deliverables**
1. Declared `series` envelope (`x`/`y`) so ANY dataset-shaped tool reuses the tab.
2. `SeriesArtefactTab` — chart + stat row + citation + empty states.
3. Sortable virtualised table + CSV export.
4. ChatShell wiring + auto-focus.
5. Elicitation trigger — form before interrogation.
6. **10/10 real-stream verification** + `aiplatform prices probe`.

## Model Assignment

Ids verified against the current model list (Opus 4.8 `claude-opus-4-8`, Sonnet 5 `claude-sonnet-5`).

| Stage | Model | Why |
|-------|-------|-----|
| Planning | `claude-opus-4-8` | Whole-system judgement; the design doc's open questions are live. |
| M1 series envelope | `claude-sonnet-5` | Contained backend shape change, spec complete, strong tests exist. |
| M2 chart tab | `claude-opus-4-8` | Visual/UX judgement + the A2UI dataModel render path (recurring trap class). |
| M3 table + CSV | `claude-sonnet-5` | Mechanical, well-specified, easily unit-tested. |
| M4 ChatShell wiring | `claude-opus-4-8` | "A2UI won't render in the Workspace" is THE recurring bug here; needs the playbook. |
| M5 elicitation trigger | `claude-opus-4-8` | Open question (tool-side vs instruction-side) — a spike, not a port. |
| M6 verification | `claude-opus-4-8` | Judging a live pass rate + resisting the urge to call it done. |

## Milestones

### M1 — Series envelope (backend, 0.25d)
Generalise `a2ui_entsoe_render.py`'s dataModel to the declared series shape.
- `kind: "series"`, `x: {key,label,type}`, `y: [{key,label,unit}]` alongside existing rows/stats/sourceUri.
- Keep `biddingZone`/`startDate`/`endDate` (the citation chip renders the range).
- **Criteria:** existing 7 tests pass; new test asserts `x`/`y` declared; `is_render_payload_tool` still True; `render_for_emit` still returns messages.
- **Risk:** low. Don't break the offload exemption.

### M2 — `SeriesArtefactTab` chart (frontend, 0.75d)
New `frontend/src/components/workspace/SeriesArtefactTab.tsx`, pattern-matched to `ClausesArtefactTab`/`SourcesArtefactTab` (read `state.surface.dataModel.get("/")`).
- Line chart per `y` key vs `x`; **emphasise the zero line** (negative prices are real: −7.6 EUR/MWh observed).
- Stat row from server `stats` (NEVER re-derive client-side — the agent quotes the same numbers).
- Citation chip: `bq://` source **and the rendered date range**.
- Empty state: NULL/unsettled range → "no settled prices in this range", never blank (NEVER SILENT).
- **Charting:** default to hand-rolled SVG (~60 LOC, zero dep, one chart type). Only reach for a lib if multi-series forces it. Lazy-load the tab.
- **Criteria:** renders from a fixture dataModel; negative values plot below zero; empty state; **no raw ids rendered**.

### M3 — Table + CSV (frontend, 0.5d)
- Virtualised sortable table beneath the chart.
- Export CSV of the visible series.
- **Criteria:** sort asc/desc; CSV contents match visible rows incl. header; >1000-row guard shows "showing first N of M" (never silent truncation).

### M4 — Wiring (frontend, 0.25d)
- `ChatShell`: `a.kind === "prices"` → `SeriesArtefactTab`; auto-focus on arrival (CLAUDE.md #7).
- **Criteria:** tab appears + auto-focuses when a prices artifact registers.
- **Read first:** `frontend/src/components/protocols/CLAUDE.md` + `backend/adk/CLAUDE.md` — diff against the known-good 7.3/7.5 path rather than re-deriving.

### M5 — Elicitation trigger (backend, 0.5d)
Resolve design Open Question #1 with a spike, then implement.
- **Preferred (a):** `entsoe_day_ahead_prices` returns a `needs_input` elicitation envelope when `bidding_zone`/dates are missing — same shape as `map_ppa_obligations`' `needs_assumptions`, which already renders a form.
- **Fallback (b):** instruction-side `request_confirmation` if the model won't call an under-specified tool.
- Date defaults must come from `wrap_with_today`, never a guess.
- **Criteria:** "what were prices?" with no params → form on the FIRST turn, 0 clarifying questions; submitting it runs the query.

### M6 — Verification (0.25d) — **the gate**
- `aiplatform prices probe <zone> --start --end` (~0.25d, Click subcommand + httpx + unit test).
- **Stream the exact prompt against deployed dev 10×.** Per run assert: `entsoe_day_ahead_prices` called; `A2UI_SURFACE` on the wire; non-empty text; **no** artifact id / `gs://` / JSON blob in prose; date range matches the request.
- **Criteria:** report a **pass rate**. Ship the demo card at **10/10 only**. Anything less → pull the card, don't hope.

## Quality Gates

Per milestone: backend `make lint && make test-fast`; frontend `npm run quality:check`.
Sprint end: M6's 10/10 live gate. **jsdom green ≠ done** — three cards were called "verified" on that basis this week and all three failed live.

## Risks

| Risk | Mitigation |
|------|-----------|
| Model won't call a tool with missing args → (a) can't fire | M5 is a spike first; fallback (b) is specified |
| Chart lib bloats the bundle | Hand-rolled SVG default; lazy-load |
| Prices tab doesn't register (the recurring A2UI trap) | M4 reads both CLAUDE.md playbooks; verify on a REAL stream, not jsdom |
| Numbers on the chart disagree with the agent's prose | Both read server-computed `stats`; client never re-derives |
