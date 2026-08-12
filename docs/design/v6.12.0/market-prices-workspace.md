# Market Prices Workspace — explore and chart the data, don't just read a summary

**Status**: Planned
**Priority**: P1 (the BigQuery/ENTSO-E journey is the first ONE demo that works end-to-end; this is what makes it worth showing)
**Estimated**: ~2.5 days
**Scope**: Fullstack — backend (elicitation trigger + series shaping), frontend (a bespoke prices tab: chart + table + export)
**Dependencies**: v6.12.0 [mcp-elicitation-adoption.md](mcp-elicitation-adoption.md) (the elicitation envelope this reuses); the shipped `entsoe_day_ahead_prices` mapping (`adk/a2ui_entsoe_render.py`, `e108b89`)
**Created**: 2026-07-17
**Last Updated**: 2026-07-17
**Motivated by**: Mark, testing on `test` 2026-07-17 — *"when the query bigquery works then I want a new workspace tab to appear so as well as the AI answer the human can explore the data and even make charts/plots from the returned data"* and *"could queries to bigquery be more form like instead of me asking for it to create a form"*.

## Problem Statement

`entsoe_day_ahead_prices` now genuinely works: on `test` it returned real DK1 day-ahead prices from BigQuery via the deployed SA. It is the **first ONE demo journey to complete end-to-end**. But what the user gets back is a dead end in two ways, both observed in a real session:

1. **The data arrives as prose, then vanishes.** The agent replied with three numbers (avg 55.3, min −7.6, max 140.2 EUR/MWh) and pointed at an artifact id. ~1000 hourly prices — a *time series*, the one shape that is useless as prose and obvious as a chart — were summarised into three scalars. The analyst cannot see the shape, spot the negative-price hours, or compare a PPA strike against the curve. The thing they'd actually do next (plot it) is impossible in-product.

2. **Getting the query to run is an interrogation.** Observed verbatim: *"can you query bigquery for prices?"* → *"provide the bidding zone and dates"* → *"make a form for me"* → *"what fields would you like?"* → *"the things you needed for the bigquery"* → a form. Five turns to ask three questions the tool's own signature already declares (`bidding_zone`, `start_date`, `end_date`). The elicitation primitive exists and works — it simply isn't *offered* until the user knows to demand it by name.

A third, related defect is already fixed and is the foundation this builds on: the series had **no result→A2UI mapping**, so it blew the 50K offload threshold, got dumped to an artifact, and the agent read a raw artifact id aloud. Registering the mapping (`e108b89`) made it offload-exempt and gave it a tab. **That tab currently renders a summary card.** This doc is about making the tab worth opening.

### Is this a one-off or a pattern?

A pattern, and worth fixing as one. Every mapped tool so far renders a *document-shaped* result (clauses, comparison, obligations, sources) — lists of typed facts. `entsoe_day_ahead_prices` is the first **dataset-shaped** result: N rows × M numeric columns over time. `captured_rates`, the load/solar/wind columns already sitting in the same BigQuery tables, and any future BigQuery skill are the same shape. If we hand-roll a chart for prices, we will hand-roll another for load, and another for captured rates — the "incremental special-casing" anti-pattern. So: design a **series surface** that any dataset-shaped tool result can populate, and let prices be its first consumer.

Equally, the elicitation gap is not ENTSO-E's. *Any* tool with required scalar params has it. The fix belongs at the "tool needs params it doesn't have" boundary, not in the PPA expert's prompt.

## Goals

**Primary goal:** a BigQuery price query lands as an **explorable Workspace tab** — chart + table + the numbers — that the analyst can read, sort, and export, while the agent keeps its short prose answer in chat.

**Success metrics:**
- Asking for prices with **no params** yields a form on the **first** turn (0 clarifying questions, down from an observed 4).
- A successful query yields a tab containing a **line chart** of the series, auto-focused, within one turn.
- The analyst can export the visible series to CSV without asking the agent.
- The agent's chat answer never contains an artifact id, a `gs://` path, or a JSON blob (regression guard — all three shipped to a user this week).
- The prices journey passes **10/10** identical runs on a real stream before it is advertised on a demo card.

**Non-goals:**
- A general BI tool. No pivot builder, no joins, no arbitrary SQL from the user.
- User-authored charts of *arbitrary* data. The surface charts a **declared series shape**; it is not a chart-anything canvas.
- Replacing the agent's analysis. The chart is for the human; the agent still answers in prose.

## Axiom Alignment

| # | Axiom | Score | Note |
|---|-------|-------|------|
| 1 | INSTANT FEEL | +1 | The tab renders from the `updateDataModel` already on the wire — no second round-trip, no re-query. The form removes 4 latency-laden clarifying turns. |
| 2 | EARNED TRUST | +1 | The chart shows the *actual* returned rows with the `bq://` citation, instead of three model-summarised scalars taken on faith. Directly addresses the wrong-year incident: the range is rendered, so a wrong range becomes *visible*. |
| 3 | SKILLS, NOT FEATURES | +1 | The series surface is declared by a tool's result mapping, so any dataset-shaped skill gets it. Not a bespoke "prices feature". |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Charting is deterministic client code, not tokens. The model stops trying to hand-render a table (which it did, badly, in raw JSON, on 2026-07-17). |
| 5 | GRACEFUL DEGRADATION | +1 | The Basic-catalog summary card remains the fallback for any generic render; the rich tab is an enhancement, not a dependency. Empty/NULL-price ranges render an explicit empty state. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Reuses the registered result→A2UI mapping + `updateDataModel`, and the **MCP-standard** elicitation envelope from [mcp-elicitation-adoption.md](mcp-elicitation-adoption.md). No new wire format. |
| 7 | API FIRST | +1 | The series shape is the tool's typed result; the tab is a pure function of it. CLI-previewable (`aiplatform a2ui render entsoe-prices --result f.json`). |
| 8 | OBSERVABLE BY DEFAULT | 0 | Neutral — rides existing tool/latency instrumentation. |
| 9 | SECURE BY CONSTRUCTION | 0 | Neutral. No new data access: same tool, same SA, same IAM. ENTSO-E market data is not customer-confidential; export is of data already on the user's screen. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The client renders a declared series; the server decides what a series *is*. The tab holds no domain knowledge of PPAs or ENTSO-E. |

**Net score: +8** (threshold ≥ +4 ✅). No axiom scores −1. Hard-fail rules: EARNED TRUST is +1 ✅; SECURE BY CONSTRUCTION is 0 with no new data access ✅.

## Design

### The shape: a declared "series" surface

The shipped mapping already emits the full series in `updateDataModel`:

```jsonc
{ "biddingZone": "DK1", "startDate": "2026-06-01", "endDate": "2026-06-07",
  "rowCount": 168, "stats": {"avg": 141.0, "min": -7.6, "max": 140.2},
  "sourceUri": "bq://…", "rows": [{"ts": "…", "price_eur_mwh": 141.01}, …] }
```

Generalise this into a **series envelope** any dataset-shaped tool can emit, so the tab is not ENTSO-E-specific:

```jsonc
{ "kind": "series",
  "title": "DK1 day-ahead prices",
  "x": { "key": "ts", "label": "Time", "type": "time" },
  "y": [ { "key": "price_eur_mwh", "label": "Price", "unit": "EUR/MWh" } ],
  "rows": [], "stats": {}, "sourceUri": "bq://…" }
```

`y` is a **list** so the same surface later charts load/solar/wind (already columns in the same tables) with no new component. The mapping (`a2ui_entsoe_render.py`) declares the axes; the tab reads them. This is the one decision that stops the next BigQuery skill needing a new tab.

### Frontend — `SeriesArtefactTab`

A bespoke tab (the proven pattern: `ClausesArtefactTab`, `SourcesArtefactTab` — Basic v0.9 has no chart or table component, so rich rendering reads the `dataModel`), wired in `ChatShell` on `a.kind === "prices"` → later any `kind === "series"`:

- **Line chart** of each `y` series against `x`. Zero-line emphasised — negative prices are real and analytically interesting (the observed run hit −7.6 EUR/MWh).
- **Stat row** — avg / min / max, from `stats` (server-computed; the client never re-derives numbers the agent also quotes, or the two will disagree).
- **Table** — virtualised, sortable, the raw rows beneath the chart.
- **Export CSV** of the visible series.
- **Citation chip** — the `bq://` source, and the **rendered date range** (so a wrong range is visible, not silent — see the 2024/2026 incident).
- **Empty/degraded states** — a NULL-priced (unsettled) range renders "no settled prices in this range", never a blank pane (NEVER SILENT, CLAUDE.md #8).

**Charting library:** decide in implementation. Requirements: no new heavy dep if an existing one serves; render server-declared axes; keep charting code out of the main bundle (lazy-load the tab). Candidates: Recharts vs a hand-rolled SVG line plot (~60 lines, zero dep) given we need exactly one chart type. **Prefer hand-rolled SVG unless multi-series/zoom forces otherwise.**

### Backend — offer the form before the interrogation

The elicitation *mechanism* is owned by [mcp-elicitation-adoption.md](mcp-elicitation-adoption.md) — one envelope, MCP-standard `requestedSchema`, rendered as the existing A2UI chat form. This doc adds only the **trigger**: when a tool needs required scalar params the turn hasn't supplied, raise the elicitation **instead of** asking in prose.

**Settled in implementation: (a), with the tool's params made optional by signature — see [Open Questions #1](#open-questions) for the evidence.** The two candidates as originally framed:

- **(a) Tool-side (preferred, and chosen).** `entsoe_day_ahead_prices` returns a `needs_input` elicitation envelope when `bidding_zone`/dates are missing — exactly the shape `map_ppa_obligations` already returns for `needs_assumptions`, which already renders a form today. Deterministic, testable, not prompt-dependent. Requires the model to call the tool with missing args, which it may avoid — *and which a required-param declaration makes illegal outright; that turned out to be the real bug (OQ #1).*
- **(b) Instruction-side.** Tell the PPA expert to call `request_confirmation` when it lacks params. Simpler, but prompt-dependent — and this week's evidence is that instructions get ignored under load (the agent hand-authored A2UI despite an explicit prohibition, on `pro`).

Prefer **(a)**: same proven path as the obligation form, and it cannot be ignored by a model having an off day. Fall back to (b) only if the model won't call a tool it knows is under-specified.

**Date grounding** (`wrap_with_today`, shipped `e108b89`) is a prerequisite: the form's date defaults must be real (`start of year` → 2026-01-01), not a training-era guess.

## Implementation Plan

| # | Task | Est |
|---|------|-----|
| 1 | Generalise the mapping's dataModel to the series envelope (`x`/`y` declarations); update tests | 0.25d |
| 2 | `SeriesArtefactTab` — chart + stat row + citation + empty states | 0.75d |
| 3 | Virtualised sortable table + CSV export | 0.5d |
| 4 | Wire in `ChatShell` (`kind: "prices"` → series tab), auto-focus on arrival | 0.25d |
| 5 | Elicitation trigger (a): `needs_input` envelope from the tool + render + tests | 0.5d |
| 6 | **Verification: 10/10 real-stream runs** + `aiplatform prices probe` | 0.25d |

**Total: ~2.5d**

### CLI Surface

`aiplatform a2ui render entsoe-prices --result <file.json>` already previews the mapping headlessly (existing command, no new work). Add one:

- `aiplatform prices probe <zone> --start <d> --end <d>` — run the tool against the deployed env; print row count, stats, source URI. Rationale: today, checking whether the BigQuery path works end-to-end requires impersonating the SA and hand-writing a REST call (exactly what was needed to prove the fix on 2026-07-17). That should be one typed command. (~0.25d, folded into task 6.)

## Migration & Rollout

No schema change, no data migration. The mapping is already registered and live on dev. Rollout is a code deploy + the standard platform-skill seed. Rollback = revert the tab wiring; the summary card remains.

**Feature flag:** not required — the tab is additive and only appears when a prices result exists.

## Testing Strategy

**Backend (pytest):** series-envelope shape; NULL-price ranges; empty ranges; error payloads render nothing; `needs_input` envelope raised when params are missing; offload exemption stays true.

**Frontend (Vitest):** tab renders chart + stats from a fixture dataModel; sorting; CSV contents; empty state; **no raw ids rendered**.

**The verification bar (non-negotiable, and the lesson of this week):** jsdom green ≠ it works. Three demo cards were advertised as "verified" on the strength of isolated tool tests and jsdom renders; all three failed live — silently. Before this journey is advertised anywhere:

1. Stream the **exact** user prompt against deployed dev **10 times**; record a **pass rate**, not an impression.
2. Assert per run: `entsoe_day_ahead_prices` called; a tab artifact registered (`A2UI_SURFACE` on the wire); non-empty text; **no** artifact id / `gs://` / JSON blob in the prose; the date range matches the request.
3. Ship the card only at **10/10**. Anything less gets pulled, not hoped over.

## Security Considerations

No new data access: same tool, same SA, same IAM grants (recorded in [env-config-parity.md](../../ops/env-config-parity.md) §7). ENTSO-E market data is **not** customer-confidential — unlike the PPA corpus — so the CLAUDE.md derivative-artefact rule (thumbnails/snippets of private contracts) does not bite here. CSV export contains only rows already rendered to the authenticated user. The tab must never render a raw doc-id or `gs://` path (CLAUDE.md #9).

## Performance Considerations

`_MAX_ROWS = 1000` caps the series (~6 weeks hourly). The rows are already on the wire in `updateDataModel` — the tab costs no extra fetch. Virtualise the table; lazy-load the tab so charting code stays out of the main bundle. A year of hourly data (8760 rows) exceeds the cap: surface "showing first 1000 of N" rather than truncate silently (NEVER SILENT), or downsample. See Open Questions.

## Success Criteria

- [ ] A price query with no params raises a form on the first turn (0 clarifying questions).
- [ ] A successful query auto-focuses a Workspace tab with a line chart of the series.
- [ ] Stat row matches the agent's prose numbers (both from server-computed `stats`).
- [ ] Table sorts; CSV export matches the visible series.
- [ ] Citation chip shows the `bq://` source AND the rendered date range.
- [ ] Negative prices and NULL/unsettled ranges render correctly (not blank).
- [ ] No artifact id, `gs://` path, or JSON blob in chat prose.
- [ ] **10/10 real-stream runs pass** before the demo card is re-advertised.
- [ ] A second dataset-shaped tool could reuse the tab by declaring `x`/`y` only (design review, not code).

## Open Questions

> **#1 RESOLVED (live evidence, 2026-07-17) — it needed BOTH (a) and (b).**
> The ADK declaration showed all three params `required` (no defaults), leaving the
> model two legal moves: invent values (→ the wrong-year answer) or interrogate
> (→ 5 turns). Making them optional (a) killed the *inventing* half — the main
> journey then passed **10/10 live with the correct year**. But a live zero-arg run
> proved the model still **won't** call a bare tool: it replied "which bidding zone
> and what date range?" and called nothing. So (a) alone is insufficient and (b) —
> an instruction to call the tool immediately, with nothing if need be — is
> required on top. Unit tests could not have caught this: the tool *does* return a
> form when called bare; the model simply never called it.


1. **Elicitation trigger (a) vs (b)** — **RESOLVED (2026-07-17, task 5): (a) tool-side, plus a one-line signature change that is the actual fix.**

   The spike asked the question the wrong way round. "Will the model call a tool with knowingly-missing args?" — it *cannot*, and that was never a model-judgement problem. `entsoe_day_ahead_prices` declared its three params without defaults, so ADK's generated `FunctionDeclaration` marked all three `required`:

   ```
   entsoe_day_ahead_prices -> required: ['bidding_zone', 'start_date', 'end_date']
   map_ppa_obligations     -> required: []          # the form that DOES fire live
   ```

   A required-param schema leaves a well-behaved model exactly two legal moves: **invent** the values (this is the wrong-year incident — the model filled the gap rather than break the schema) or **interrogate** in prose (this is the 5-turn transcript). Both observed defects are the *same* root cause, and neither is a prompt failure. The contrast with `map_ppa_obligations` — every param optional, `required: []`, and its `needs_assumptions` form fires reliably today — is the whole proof: the proven path is optional-by-signature.

   So (a) works, conditional on making the params optional (`bidding_zone: str = ""`, …). The model then has a third, better move, and the refusal is deterministic tool code rather than a prompt the model can have an off day about.

   (b) is **rejected as the primary**, on this week's evidence that instructions get ignored under load (the agent hand-authored A2UI on `pro` despite an explicit prohibition). It would also leave the wrong-year path open — an instruction to ask does not stop a model from guessing. The one instruction-shaped piece we *did* keep is in the **tool's own docstring** ("call this even when you don't have the zone or the dates; never invent them"), which rides on the `FunctionDeclaration` at the call-decision point, travels with the tool to every skill that lists it, and cannot be dropped by a skill prompt. That is tool-side, not (b).

   **Shipped:** missing/empty params → a `needs_input` envelope (the shape `map_ppa_obligations` returns) with `bidding_zone` (a select over the real `_ZONE_TABLES`/`_COUNTRY_TABLES` list, offered as friendly `DK1 — West Denmark` labels resolved back to the code per CLAUDE.md #9) + `start_date`/`end_date` defaulting to the last 7 days **computed per call from the real clock** (never a literal — asserted by a test that no hardcoded year can pass). Supplied params are prefilled, so the form asks only for what's missing. On submit, the re-run reads the values off the surface data model via `read_submitted_values` — the same no-transcription closed loop as the obligation form. One trap paid down for the next adopter: the render registry gates on **tool name**, so a tool that starts returning an envelope renders nothing until the shared transform is registered for its name — `register_elicitation_for(tool_name)` now does that in one line, registered *before* the success mapping (first match wins).
2. **Charting dep** — hand-rolled SVG (zero dep, one chart type) vs Recharts (multi-series, zoom, free polish). Default to hand-rolled; revisit if multi-series `y[]` lands.
3. **>1000 rows** — surface "showing first 1000 of N", or downsample server-side (hourly → daily mean) and say so? Truncating silently is not an option.
4. **Tab identity** — one "Prices" tab where the latest query wins (current, matches `ppa_comparison`), or one tab per zone/range so an analyst can compare DK1 vs DK2 side-by-side? The latter is the real analyst workflow but multiplies tabs.

## Related Documents

- [mcp-elicitation-adoption.md](mcp-elicitation-adoption.md) — the elicitation envelope + MCP standard this reuses (owns the mechanism; this doc owns the trigger).
- [tool-results-as-a2ui.md](../v6.7.0/implemented/tool-results-as-a2ui.md) (7.3) — the result→A2UI mapping path.
- [workbench-artifacts-model.md](../v6.7.0/implemented/workbench-artifacts-model.md) (7.5) — artifact → tab + Home index.
- [workbench-home-and-curated-activity.md](../v6.11.0/workbench-home-and-curated-activity.md) — the Workspace/Home index this tab appears in.
- [backend/adk/CLAUDE.md](../../../backend/adk/CLAUDE.md) — the A2UI emission playbook + traps (read before touching the mapping).
- [env-config-parity.md](../../ops/env-config-parity.md) §7 — the BigQuery IAM this journey depends on (per-env, not carried by a code merge).
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI conventions for `aiplatform prices probe`.
