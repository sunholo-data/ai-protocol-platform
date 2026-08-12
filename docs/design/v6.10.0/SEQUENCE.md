# v6.10.0 Build Sequence — Handoff Unification

**Gate:** v6.8.0 shipped its UX (elicitation primitive, full-switch completion) but
2026-07-15 live testing showed the handoff *mechanism* is unreliable: the door's
lite model conflates the two handoff tools (`transfer_to_agent` vs
`request_handoff`), and the switch violates the session↔skill binding gate
(post-switch form submits 403). User direction (2026-07-15): *"one handoff tool
… a better approach that integrates more with ADK — step back and consider a
refactor."*

**Theme:** *One tool, one policy point, one completion path.* Delete the custom
`request_handoff` machinery; every handoff is ADK's native, enum-constrained
`transfer_to_agent`, with the confirmation floor enforced deterministically in a
single `before_tool_callback` (auto → native in-turn transfer; confirm/cwf →
elicitation card → the shipped full switch). The session index follows the
conversation so the security gates hold after a switch. Acceptance is three
scripted end-to-end flows (auto / confirm / confirm-with-form) run against
deployed envs — the definition of done, not an afterthought.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 10.1 | [unified-adk-handoff.md](unified-adk-handoff.md) | **P0** | ~2.5d | 8.1 ✅, 8.2 full-switch frontend ✅ | Single ADK-native handoff verb (enum-constrained `transfer_to_agent`, slug-readable agent names), floor-as-policy in one `before_tool_callback`, stub sub_agents for confirm-floor jobs, session index follows the most recent turn's skill (fixes the post-switch 403), deletes `request_handoff` + the dead backend confirm branch. Ships with `make handoff-e2e`. Net axiom **+8**. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 10.1 | [unified-adk-handoff.md](unified-adk-handoff.md) | Planned 2026-07-15 (P0 — blocks ONE user testing) |

## What ships in v6.10.0

- **10.1** — one handoff tool (ADK-native), all three levels (auto / confirm /
  confirm+form) working and E2E-tested on deployed dev+test; ~150 LOC of custom
  handoff machinery deleted; post-switch surface actions no longer 403; replayed
  confirm cards render frozen.

## Dependency Graph

```
8.1 elicitation primitive (shipped) ─┐
8.2 full-switch frontend  (shipped) ─┴─→ 10.1 unified-adk-handoff
```
