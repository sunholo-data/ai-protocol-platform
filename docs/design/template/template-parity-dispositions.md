# Parity gap — recorded dispositions

**Status**: Living record · **Sprint**: TEMPLATE-INVERT M2 · **Created**: 2026-08-17
**Design doc**: [template-repo-topology.md](template-repo-topology.md)

`make template-parity` says *how many* files the sanitizer rewrites.
`make template-triage` proposes what to do with each. **This file records the
decisions**, so M3 and M4 inherit the judgment instead of re-deriving it — and
so a later reviewer can see what was decided and why.

## The rule that resolved almost everything

> A doc whose **SUBJECT** is the customer → **move downstream**.
> A doc that **MENTIONS** the customer → **ships, scrubbed**.

The first triage pass ignored that distinction and recommended moving **60**
files. Most were wrong, in the damaging direction. Two heuristics caused it:

- **`docs/ops/` as a move-prefix.** It swept the whole ops surface downstream —
  including [docs/ops/gotchas.md](../../ops/gotchas.md), whose first line reads
  *"Platform Gotchas: operational surprises that have burned us or the AIPLA
  fork."* That file is written **for forks**. the internal-tools fork #17/#21 are asking for
  *more* of this class, not less. Moving it would have been a straight
  regression for every downstream consumer.
- **"customer-identity appears in a doc" as a move-trigger.** It swept ~40
  platform design docs (A2UI surfaces, ADK handoff, maps grounding, compaction)
  whose only sin is naming the customer as their first consumer.

Corrected split: **~2 move-downstream, 11 config-drive, 307 scrub-in-place, 18
review** — the bulk is M4's mechanical work, not M2's.

## Moved downstream (M2, done)

| Was | Now | Why |
|---|---|---|
| `docs/design/v6.4.0/multi-tenant-demo-readiness.md` | `docs/customers/one/demo-readiness.md` | Title is literally *"ONE Demo Readiness — This Deployment Is Acme Energy"*. Definitionally deployment-specific. 64 customer tokens in 938 lines. |
| `…/multi-tenant-demo-readiness-sprint.md` | `docs/customers/one/demo-readiness-sprint.md` | Its sprint companion. |

`docs/customers/` is now a single glob exclusion in the sanitizer, so a new
customer doc is excluded **the day it is filed** rather than when someone
remembers to list it.

Eight platform docs linked to the moved design doc. A platform doc must not
link to a deployment-private one — the target does not exist upstream, so the
link dangles in the published template (the internal-tools fork #11). All eight were
**de-linked**: the descriptive context stays, the markdown link wrapper goes.
That form is correct in both tiers.

## Renamed, not moved (M2, done)

`docs/design/v6.6.0/one-app-fork-convergence{,-sprint}.md` →
`fork-convergence{,-sprint}.md`, with all **nine** referrers updated.

These were customer-*named* but generic platform design (model tiers, Skill
Studio copilot, read-aloud, per-skill persona). They were the only two entries
in `GLOB_KEEP`, kept because renaming mid-release was judged too disruptive and
deferred to "the `docs/customers/` restructure". This milestone **is** that
restructure, so the deferral was cashed in.

`GLOB_KEEP` is now empty — and stays declared, because the globs are
deliberately broad and a future legitimate over-match should be listed there
rather than narrowing the glob. That keeps the default at *delete unless
explicitly kept*.

## Config-drive (M3)

The 11 files that must hold a **real** value here and a placeholder upstream.
Plus one mechanism that has to change:

### The `.example`-rename trick does not survive the inversion

The sanitizer currently handles two files with a **third** mechanism, distinct
from delete and scrub:

```
infrastructure/mcp-toolbox/tools.yaml      <- DELETE real, RENAME tools.example.yaml over it
docs/ops/deployed-urls.md                  <- DELETE real, RENAME deployed-urls.example.md over it
```

That works for a one-shot copy and **cannot** work upstream/downstream: it
produces the same tracked path holding generic content upstream and real
content downstream, which is the permanent-merge-conflict case the whole design
exists to eliminate. Both correctly show up in the parity gap.

M3 must replace it: the **shipped** path is the `.example` one, and the real
file is downstream-only (gitignored upstream, or generated from the example at
build time when absent). The `Dockerfile`'s `COPY tools.yaml` and the
cloudbuild sidecar's `--config=/app/tools.yaml` both depend on the real name,
so the generation step has to run before the image build.

## Scrub in place (M4)

307 files. Mechanical, and the existing `TOKEN_REPLACEMENTS` table already
specifies every substitution and keeps the suites green by replacing both sides
of each assertion.

The 18 flagged for review all resolved to **scrub-in-place** except
`infrastructure/mcp-toolbox/tools.yaml` (config-drive, above):

- **9 test files** (`test_cli_client.py`, `test_admin_clients.py`,
  `branding.test.ts`, `AnalyticsPage.test.tsx`, …) use the customer domain as a
  fixture value. Platform tests, customer-flavoured data.
- **`docs/ops/rag-corpus-bucket-grants.md`** — a genuine platform runbook (the
  Vertex RAG service agent needs `objectViewer` on the source bucket) that uses
  the customer bucket as its worked example. The knowledge is transferable; the
  bucket name is not.
- **`docs/design/v6.23.0/SEQUENCE.md`** scores highest of the docs (12.9
  tokens/100 lines) yet is a platform version index. It is the clearest proof
  that density is a *signal*, not a rule — which is why the triage tool now
  routes high-density files to `REVIEW` rather than auto-recommending a move.
  It also references docs the sanitizer deletes, so M4 must fix those links.

## Not in the gap, already handled

`GLOB_DELETIONS` removes 253 files outright (customer skill templates, UAT
records, fork scope docs, `one-*` ops scripts, the obligation fixtures). Those
need no disposition — deletions are exactly what the inversion is built on.
