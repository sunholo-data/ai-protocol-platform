# v6.12.0 Build Sequence — MCP Elicitation Adoption

**Origin:** 2026-07-16 — Mark, reviewing the elicitation work: *"like claude-code
questions, and I think MCP supports elicitations as well?"* Correct on both. We
hand-rolled an elicitation primitive (`request_confirmation` + `ElicitationField`)
that is functionally MCP's standard `elicitation/create`, and our MCP client
doesn't yet accept elicitations from connected servers.

**Theme:** *One elicitation envelope, standards-aligned, sourced three ways.*
Adopt the MCP `elicitation` capability at the MCP-client boundary and align our
internal envelope to the spec's `requestedSchema`, so a tool, an agent
(`request_confirmation`), OR a connected MCP server can all raise the SAME A2UI
chat form. Protocol-Over-Custom (Axiom #6); zero frontend render changes.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 1 | [mcp-elicitation-adoption.md](mcp-elicitation-adoption.md) | P1 | ~2d | v6.7.0 tool-input-elicitation-a2ui, v6.8.0 elicitation-in-chat-primitive (both shipped) | MCP client capability + bidirectional schema translator + wire the dormant `request_confirmation`. Backend-only; frontend reuses the A2UI chat form. |
| 2 | [market-prices-workspace.md](market-prices-workspace.md) | P1 | ~2.5d | #1 (elicitation envelope); `entsoe_day_ahead_prices` mapping (shipped `e108b89`) | The first DATASET-shaped result. Explorable/chartable series tab + raise the form BEFORE interrogating the user. Fullstack. |

## Timeline estimate

| Doc | Status | Est |
|-----|--------|-----|
| mcp-elicitation-adoption.md | Planned | ~2d (0.5 translator · 1 inbound capability · 0.5 AI-authored path + polish) |
| market-prices-workspace.md | Planned | ~2.5d (0.25 series envelope · 0.75 chart tab · 0.5 table+CSV · 0.25 wiring · 0.5 elicitation trigger · 0.25 10/10 verification) |

## What ships in v6.12.0

- The MCP client declares `{"elicitation": {}}` and renders a connected server's
  `elicitation/create` as an A2UI chat form, returning a spec-correct
  `ElicitResult` (accept/decline/cancel).
- `ElicitationField` ↔ MCP `requestedSchema` round-trips losslessly.
- `request_confirmation` is switched on (AI-authored forms) for at least one skill.
- A BigQuery price query lands as an **explorable Workspace tab** (chart + sortable
  table + CSV export + `bq://` citation) instead of three scalars and an artifact id,
  and asking for prices with no params raises the form on the FIRST turn.
- The series surface is declared (`x`/`y`), so the next dataset-shaped tool reuses it.

## Dependency Graph

```
v6.7.0 tool-input-elicitation-a2ui ─┐
                                    ├─► v6.12.0 mcp-elicitation-adoption
v6.8.0 elicitation-in-chat-primitive┘
```
