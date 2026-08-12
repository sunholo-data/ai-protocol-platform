# v6.19.0 — Build Sequence

Close the 11 open items from the CPH Uni AIPLA fork's upstream-feedback log
before the public template refresh. Not a feature release: this is fork-readiness
plus one genuine confidentiality fix.

Context: all 45 entries in `cphu-aipla-app/docs/upstream-feedback.md` were
triaged on 2026-07-29 against `44ebdff` — 29 already fixed, 3 partial, 11 open,
2 no-action. The triage record and the ranked list live in
[template-fork-ergonomics.md](../template/template-fork-ergonomics.md).

**Why now:** the template refresh is queued and a second commercial fork is
pending. Every one of these ships into that fork if we publish first.

## Ordering

| # | Doc | Priority | Est. | Depends on | AIPLA items | Notes |
|---|-----|----------|------|------------|-------------|-------|
| 1 | [stream-boundary-invariants](stream-boundary-invariants.md) | P0 | ~1d | None | #39 | **A confidentiality hole** — privileged tool results reach the client stream. Ships first regardless of the rest. (#32 was descoped 2026-07-29: already fixed in `adk/agui.py`) |
| 2 | [fork-ready-defaults](fork-ready-defaults.md) | P0 | ~2.5d | None | #16, #17, #18, #36, #42 | The five things a new fork hits first, all failing silently. #36 (CI gate) should land before the others so the rest deploy behind a gate |
| 3 | [multi-audience-auth](multi-audience-auth.md) | P1 | ~1d | None | #33, #34 | Prevention, not a bug fix — the symptom doesn't reproduce upstream. Same footgun has fired 5× in the one fork we can observe |
| 4 | [production-semantics-in-tests](production-semantics-in-tests.md) | P1 | ~1d | None | #35, #37b/c | Closes the CI blind spot that let issue #38 happen a month after AIPLA documented the identical bug. **Scores +3 — see the doc's threshold note; needs a human call** |
| 5 | [client-surface-correctness](client-surface-correctness.md) | P2 | ~0.75d | None | #23, #38, #44 | Three small user-visible fixes. #38 needs a real external MCP host to verify |

No hard dependencies between docs — they can run in parallel or in any order.
The ordering above is by severity, not by blocking.

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 1 | Stream boundary: privilege gate | ~1d | Proposed |
| 2 | Fork-ready defaults: CI gate, project guard, build args, anon-group persistence, dead mount | ~2.5d | Proposed |
| 3 | Multi-audience auth seam + lint fence | ~1d | Proposed |
| 4 | Production-semantics test doubles | ~1d | Proposed |
| 5 | Client surface: layout, render stability, artefact portability | ~0.75d | Proposed |
| | **Total** | **~6.25d** | |

## What ships in v6.19.0

- **A privileged-by-default tool-result boundary** at the SSE wrapper,
  independent of whichever adapter produced the events. (The companion
  `RUN_ERROR`-terminality invariant already shipped in `adk/agui.py`.)
- **A fork that can actually deploy**: CI-gated deploys, a fail-loud (not
  brand-anchored, not fail-open) project guard, build args that can't be silently
  dropped, anonymous-group sessions that survive scale-to-zero, and no
  mounted-but-unread `/gcs_config`.
- **An explicit audience at every auth call site**, plus a lint fence and one
  canonical backend guard, so the wrong-token bug class stops recurring.
- **Test doubles that model production semantics** — session ownership and
  state-key scoping — so the two failure modes that have already burned us are
  catchable in `make test-fast`.
- **Three client-surface fixes**: chat input above the fold, no markdown subtree
  remounts, and artefacts that work in hosts we didn't write.

## Dependency graph

```
(none — all five are independent)

  1. stream-boundary-invariants   ─┐
  2. fork-ready-defaults          ─┤
  3. multi-audience-auth          ─┼─►  public template refresh
  4. production-semantics-in-tests─┤
  5. client-surface-correctness   ─┘
```

## Open questions for a human

1. **#17 `/gcs_config`** — delete the dead plumbing (recommended) or wire it up
   to runtime-swappable skills? The latter is a feature and needs its own doc.
2. **Doc 4's axiom score is +3**, below the +4 threshold, because test
   infrastructure can't align with product axioms by construction. Ship it
   standalone, or fold it into docs 1 and 2?
3. ~~Scope vs. the refresh~~ — **decided 2026-07-29: publish after all five.**

## Related

- `cphu-aipla-app/docs/upstream-feedback.md` — the source log, now annotated per-entry
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md) — triage record + ranked adoption list
- [aitana-template-publish skill](../../../.claude/skills/aitana-template-publish/SKILL.md) — now carries "re-triage downstream logs" as a standing pre-refresh step
