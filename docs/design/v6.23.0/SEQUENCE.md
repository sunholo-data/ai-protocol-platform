# v6.23.0 — Build Sequence

**Cutover hardening.** Opened 2026-08-06, straight out of the ONE user-acceptance
session ([source record](../../feedback/platform-uat-2026-08-06.md)). Unlike
v6.22.0, which came from a Google product announcement, every doc in this version
traces to a named person describing a specific failure or need on a specific date.

The framing that matters: **the UAT verdict was strongly positive.** Tomas —
*"it is amazing, the interface is a different level, kudos"* — is already pitching
the product to a prospective client and wants a demo booked. Nothing here is a
rescue. It is the punch list between a well-received test and a customer who
replaces v5 on **1 September**, a date agreed in that meeting and constrained by
Mark being unavailable for most of the following month.

Two things reorder the version relative to a naive reading of the feedback:

1. **The loudest bug was the cheapest.** Both users independently reported the
   composer never wrapping. It was a single-line `<input>` element, which cannot
   wrap at any CSS setting. Fixed the same day, with a regression test, and it is
   not in the table below because it is already done.
2. **The quietest bug was the worst — and our first diagnosis of it was wrong.**
   Tomas's *"it was like she didn't have access to the chat history"* was
   attributed to over-aggressive compaction. A live A/B disproved that within
   hours: **compaction has never run on the chat path at all**, because the
   AG-UI Runner is built without an `App`. The real defect is the inverse —
   unbounded context growth with no compaction backstop, which matches *Dana's*
   context-limit complaint rather than Tomas's. That correction is the P0.
3. **Tomas's actual cause was found later the same day, and was already fixed.**
   ag_ui_adk's SessionManager swept any session idle >20 minutes and permanently
   deleted it from Vertex — `44ca9b6`, shipped 2026-08-05, deployed the night
   before the UAT. His session was 90 minutes over 12 turns, so the sweep hit it
   mid-conversation. Tomas described it as deletion himself in the meeting. The
   same sweep explains the blank-thread-on-resume report: one root cause, two
   symptoms. **Confirmation is owed from Tomas re-running his journey — not
   from a code review.**

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [compaction-wiring-and-observability](compaction-wiring-and-observability.md) | **P0** | ~2.5d (spike ✅ + 0.5 + 1 + 1) | None | **Supersedes #1a.** Compaction has NEVER run on the chat path: `ADKAgent(adk_agent=…)` leaves `_app` None, so the Runner has no App and both triggers are dead (`runners.py:622`, `:1480`). Measured: 25 turns at `compaction_interval=10` → 100 events, 25 invocations, **0 compactions**. Failing hermetic guard committed under `make adk-conformance`. Real impact is the *opposite* of what we assumed — unbounded context growth, matching Dana's context-limit complaint. Net **+6**. |
| 1a | [conversation-context-fidelity](conversation-context-fidelity.md) | ~~P0~~ | shipped | — | **Root cause DISPROVED same day.** Tuning table + `app.py` hardcode fix are valid and shipped, but **inert for chat** until #1 lands. Kept for the reasoning-failure record: every ADK statement in it was individually correct and the conclusion was still wrong, because nothing verified the config reached the code that reads it. |
| 1b | [compaction-tuning-console](compaction-tuning-console.md) | P1 | ~2d | #1 (M1/M2/M4 shipped) | Compaction is a deep well with six interacting knobs, all currently code constants — an experiment costs a deploy. Makes `token_threshold`, `event_retention_size`, and the **summarizer model + prompt** runtime-settable from `/admin/settings`, per-skill. Feasible cheaply because `invocation_context.events_compaction_config` is a mutable per-invocation field and `platform_config` + the admin plane already exist — no ADK fork, no new plumbing. Net **+8**. |
| 1c | [compaction-strategy-hooks](compaction-strategy-hooks.md) | P1 | ~3d | #1b (shares the settings block) | The console exposes scalars; the interesting choices aren't scalars — *what is kept verbatim*, what replaces the rest, and which model does the deciding. Pluggable `CompactionStrategy` registry (same convention as `a2ui_result_render`), 5 shipped strategies, per-skill selection, and `aiplatform compaction replay/compare` for **offline A/B over recorded sessions**. Buildable because ADK's `compacted_content` is an arbitrary `Content` — a strategy may return verbatim turns, not just a summary. Extension point for template forks. Net **+9**. |
| 1d | [compaction-off-the-critical-path](compaction-off-the-critical-path.md) | P1 | ~2d | #1 (shipped) | Compaction is a model call and both ADK paths run INSIDE the user's request — pre-request adds straight to TTFT, post-invocation delays `RUN_FINISHED` so the UI stays 'working' after the answer has rendered. **A sidecar does NOT fix this** (verified: under request-based billing sidecars are throttled with the instance — CPU is per-REQUEST and the throttling unit is the INSTANCE). Real options are RUN_FINISHED-first, a separate Cloud Run service via Cloud Tasks, or instance-based billing. M0 measures before anything moves. Net **+5**. |
| 1e | [compaction-second-pass](compaction-second-pass.md) | P1 | ~2.5d | #1d (shipped — its option (b) trigger condition: "a second deferred job appears"); soft-composes with #1c | Live compaction's quality is capped by what a user will wait for; the raw events it summarises **survive in the session store**. A Cloud Tasks-driven second pass recompacts idle sessions from the raw originals on a `pro` model and **supersedes** the live summary via ADK's own subsume semantics (`_is_compaction_subsumed`, verified in pinned 1.31.1: later event wins on containment, and the next live compaction seeds from the non-subsumed winner). Also converts the ~22% silent no-op class (findings §3.2) into a retried job. Queue + no-role OIDC SA already live on dev (2026-08-10). Net **+8**. |
| 2 | [workspace-home-persistence](workspace-home-persistence.md) | P1 | ~1d | None | Dana's most-repeated request (raised 4×). A dominant workspace surface evicts the launcher, so starting a second skill costs a new chat — which compounds #1. Fix **removes** a special case: promote the surface into the existing 7.5 artifact-tab model that every other result already uses. Net **+4**. |
| 3 | [trace-completeness-and-access](trace-completeness-and-access.md) | P1 | ~1.5d (0.5 + 0.5 + 0.5) | None | Gates switching Langfuse off — a commitment already made to the customer. **Phase 1 is investigation, not implementation**: root cause of the missing entries is genuinely unknown and must be measured before Phase 2 is designed. Phase 3 pays off an explicit promise to Dana. Net **+5**. |
| 4 | [word-comment-anchoring](word-comment-anchoring.md) | P1 | ~1.5d (1d parse + 0.5d platform) | **Cross-repo** — primary work is in `ailang-parse`; file via the docparse inbox | Tomas's sharpest point, and he is right about the OOXML model. `docx_parser.ail` reads `word/comments.xml` but never correlates the `w:commentRangeStart/End` anchors — so comments arrive detached and an agent will *guess* which clause they annotate. long-stream uses this daily. Net **+6**. |
| 5 | [generated-document-outputs](generated-document-outputs.md) | P1 | ~3d (1 + 1 + 1) | **BLOCKED** on Dana's hand-off (LaTeX template, PPT master, BQ queries) | Highest leverage in the version — ONE has already built these by hand. Also closes a live confidentiality gap: the current offer workflow routes commercial content through **latexonline**, a third-party service outside the GCP edge. Highest axiom score in the version, **+9**. |
| 6 | [one-bigquery](one-bigquery.md) | P1 | ~2.5d (0.5 + 0.75 + 0.75 + 0.5) | None — **unblocks the unblocked half of #5** | F4 was filed as one blocked item; it is two. The *named* query library needs Dana's hand-off. The *exploratory* skill needs nothing from her, and is what lets her discover which queries are worth naming — so blocking both on one hand-off was a scheduling error. Today ONE's entire warehouse reach is two hard-coded queries; anything else costs a PR, a rebuild and a deploy. Config-only: a second toolset on the Toolbox sidecar we already run, plus one SKILL.md. **Amends v6.14.0's C3**, which banned the generic executor on a rationale the same doc's spike inverts — `allowedDatasets` is enforced *only* for that executor, making it strictly safer on dataset scope than the hand-authored tools shipping today. Net **+8**. |

| 7 | [maps-grounding](maps-grounding.md) | P2 | ~1d (0.5 shipped + 0.5) | None | Not from the UAT — a capability question ("can we add a Maps tool?"). Answer is yes, but **not** via ADK's built-in `google_maps_grounding`: its terms exclude EEA-billed customers, which we are. Google's documented EEA path, Maps Grounding Lite, is an ordinary remote MCP server — so it needs *no* new resolution code, coexists with FunctionTools (the native built-in cannot), and works on Claude/OpenAI skills too. Real work was one reusable primitive: **secret-bearing MCP headers** (`${ENV_VAR}` resolved from Secret Manager, never stored in Firestore), which every future authed MCP server now inherits. Outstanding: terraform (API + key) and the `placeUrl` attribution requirement, which is a **licence condition** and blocks any user-facing Maps skill. Net **+4**. |

## Timeline estimate

| # | Work | Est. | Status |
|---|------|------|--------|
| 0 | Composer wrap fix — `<input>` → auto-growing `<textarea>`, Enter/Shift+Enter, IME-safe; both composers (ChatShell + DrawerChatPane); 7 regression tests | ~0.25d | ✅ **Done 2026-08-06** |
| 1 | Wire the App into the chat Runner; tool-aware summarizer; HISTORY_COMPACTED event + Activity marker | ~2.5d | ✅ **M1/M2/M4 done 2026-08-06** (M3 fidelity check outstanding) |
| 1b | Runtime compaction levers in `/admin/settings` (threshold, retention, summarizer model + prompt), per-skill | ~2d | Planned |
| 1c | Pluggable compaction strategies + offline replay/compare; template extension point | ~3d | Proposed |
| 1d | Move compaction off the user's critical path (measure, then RUN_FINISHED-first or a separate service) | ~2d | ✅ **M0/M1/M2 done 2026-08-06** (user wait after answer 41.7s → 2.3s) |
| 1e | Second-pass recompaction of idle sessions from raw events (Cloud Tasks → internal OIDC route → superseding append) | ~2.5d | Proposed (infra live on dev 2026-08-10) |
| 2 | Split Workspace home from results; promote dominant surface to a Result tab | ~1d | Planned |
| 3 | Trace reconciliation (`aiplatform session reconcile`), close the gap, scoped access for Dana | ~1.5d | Planned |
| 4 | Comment↔anchor correlation in `ailang-parse`; platform consumption + skill instruction | ~1.5d | Planned (cross-repo) |
| 5 | Offer creator (LaTeX→PDF in-house), PowerPoint from ONE's master, named BigQuery library | ~3d | Blocked on hand-off |
| 6 | `one-bigquery` skill — scoped `allowedDatasets` source + generic executor on the existing sidecar, `job:true` delegation, live allowlist-enforcement test | ~2.5d | Planned |
| 7 | Maps Grounding Lite — secret-bearing MCP headers, `your-maps-project` project + per-env keys, attribution rendering, maps-assistant skill | ~1d | ✅ **dev + test live 2026-08-12** (v6.27.0; prod held — skill tag-gated to aitana-admin until prod has a real key) |
| — | **Total** | **~21.75d** | Against a 1 Sept cutover |

## What ships in v6.23.0

- **Conversations that remember what you told them.** The failure Tomas hit —
  an hour and a half of expert iteration, then a summary request that missed the
  point — stops happening, because compaction fires on token pressure rather
  than on a turn counter, and says so when it does.
- **A per-model context budget that is actually applied.** The tuning table in
  `session.py` has existed for months and has never once reached a session. This
  is also the likeliest cause of the context-limit errors Dana asked about in
  the same meeting, from the opposite direction: Claude sessions running a config
  built for a 1M-token window.
- **Conversations that get *better* while you're away.** An idle session's
  compacted history is re-derived overnight from the raw turns (which the
  session store keeps) on a model with time to think, superseding the
  latency-bounded live summary — so a resumed session remembers the specifics,
  and the ~22% of compactions that silently produce nothing get retried until
  they land.
- **A workspace you can keep working in.** The launcher stops being destroyed by
  the first result, so a second skill no longer costs a new chat — and by
  removing a special case rather than adding one.
- **A trace view good enough to switch Langfuse off**, with scoped access so
  ONE's super-users triage their own sessions instead of routing through Mark.
- **Word comments that know what they are about**, so a legal objection is
  quoted against its clause instead of guessed at.
- **The offer creator, in-app** — and ONE's commercial content out of a
  third-party web renderer.
- **A database agent ONE's analysts can actually ask questions of.** Mark named
  it in the UAT — *"you can pass it on to the web search or the database agent"*
  — and today it does not exist: the warehouse is reachable through exactly two
  hard-coded queries. `one-bigquery` gives the platform schema discovery and
  ad-hoc SQL over an explicitly allowlisted slice of ONE's datasets, so a new
  question costs a sentence instead of a deploy, and every answer shows the SQL
  it ran.

## Dependency Graph

```
#1 conversation-context-fidelity ──┐
                                   ├── independent, ship in any order
#2 workspace-home-persistence ─────┤   (#2 reduces new-chat pressure that
                                   │    makes #1 worse, so #1 before #2 if
#3 trace-completeness ─────────────┤    only one lands)
                                   │
#4 word-comment-anchoring ─────────┤── gated on an ailang-parse release
                                   │
#5 generated-document-outputs ─────┘── BLOCKED on Dana's hand-off
      │ Track C (named queries)
      ↓ authored against
#6 one-bigquery ──────────────────────  unblocked; ship ahead of #5
```

Nothing here blocks anything else inside the version. #4 and #5 have external
gates (a parser release; a customer hand-off), so start them early even though
they rank below #1–#3 — their calendar risk is higher than their priority.

#6 is the exception worth stating: it is *downstream* of #5's Track C on paper
and *upstream* of it in practice. Track C's named queries have to be authored
against a schema-aware skill, and #6 is that skill — so #6 ships first, and it
ships without waiting on the hand-off that blocks #5.

## Verification bar

Every doc in this version carries the same non-negotiable from both CLAUDE.mds:
**unit-green is not proof.** #1 in particular is prone to false confidence,
because unit tests construct sessions rather than accumulate them — the bug only
appears after a dozen real turns. Each doc names a live verification step, and
three of the five name a **person at ONE** as the acceptance test (long-stream for the
offer output and comment anchoring, Dana for the workspace and trace access,
Tomas for the context-fidelity journey he originally reported).

## Related

- [UAT source record](../../feedback/platform-uat-2026-08-06.md) — parsed transcript + notes
- [uat-triage-2026-08-06](uat-triage-2026-08-06.md) — every point raised, and where it went
- [v6.22.0 SEQUENCE](../v6.22.0/SEQUENCE.md) — the platform-services version this runs alongside
