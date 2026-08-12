# SKILL-ONBOARDING Sprint Plan

**Design doc:** [skill-onboarding.md](skill-onboarding.md)
**Sprint key:** SKILL-ONBOARDING
**Status:** Planned 2026-06-09
**Duration:** Wed 2026-06-10 → Fri 2026-06-12 (3 days, single dev)
**Scope:** Fullstack (~1270 LOC + ~30 tests)
**Runs parallel with:** 4.2 M4 CLI + smoke + rehearsal (zero file overlap — different subcommand groups)

## Goal

Close the five onboarding gaps Mark surfaced 2026-06-09: AI intro bubble, example-document picker, GCS bucket browser, ACTION-TRIGGER interactive-widget convention, and supporting CLI. So Friday demo opens Contract Expert → AI greets → user clicks an example PPA → workspace populates → user clicks a diff → agent responds.

## Day-by-day

### Wednesday 2026-06-10

**Morning — M1 SkillConfig.welcome schema + content (~1d)**
- Pydantic `WelcomeConfig` + `ExampleDocument` + `BucketBrowserConfig` (backend)
- TypeScript types matching backend (frontend)
- one-ppa-expert + one-doc-compare SKILL.md frontmatter populated
- 5 example PPAs uploaded to `gs://aitana-examples-public/ppa/`
- IAM grant `allUsers:roles/storage.objectViewer` (one-time)
- 4 backend pytest + skill-load tests

**Afternoon — M2 + M3 + M4 in parallel (Wed PM + Thu AM, ~2.5d combined)**
- **M2 SkillExamplesPicker** (~0.5d) — frontend-only; 200 LOC + 7 vitest
- **M3 AI greeting AssistantIntroBubble** (~0.25d) — frontend-only; 90 LOC + 5 vitest
- **M4 GCSFileBrowser + backend list endpoint** (~1d) — fullstack; 320 LOC + 10 tests

Independent surfaces — all 3 can fan out via Task sub-agents if velocity warrants. Single-dev sequential is fine for the LOC volume.

**Wed EOD checkpoint:** `npm run quality:check:fast` green + backend `make lint && make test-fast` green. M1+M2+M3 commits land.

### Thursday 2026-06-11

**Morning — M4 finish (if not done Wed) + M5 ACTION-TRIGGER convention + one-doc-compare interactive buttons (~0.5d)**
- `SKILL_AUTHOR_GUIDE.md` documents the surface-action convention
- one-doc-compare prompt body extended
- `compare_ppa_contracts` emits `_a2ui_actions` per diff
- KeyDifferencesPanel renders per-row "Compare clauses" buttons
- End-to-end smoke: pick 2 PPAs → click a diff → agent responds

**Afternoon — M6 CLI + buffer (~0.25d)**
- `aiplatform examples list/push` + `aiplatform bucket browse` Click subcommands
- Buffer for any Thu surfaces

**Thu EOD:** `git push origin dev` triggers Cloud Build. Poll until SUCCESS.

### Friday 2026-06-12

**Morning — final verify + sprint close**
- chrome-devtools MCP verify on live URL (curl fallback if locked, per M3.5 playbook)
- Sprint JSON M1-M6 closed `passes: true`
- Final commit `chore(sprint): SKILL-ONBOARDING M1-M6 complete + verified live`

**Afternoon — DEMO**
- Contract Expert: AI intro bubble + 5 example PPAs in picker + Contracts library in sidebar
- one-doc-compare: pick 2 PPAs → diff rendered → click "Compare clauses" → agent reacts

## Quality gates (after each milestone)

```bash
# After backend changes
cd backend && make lint && make test-fast

# After frontend changes  
npm run quality:check:fast    # lint + tsc + auth-fetch

# End of sprint
make demo-verify
```

## Cut-line (if Thursday runs hot)

**First to drop:** M5 — `ACTION-TRIGGER` convention + one-doc-compare interactive buttons. Protocol still demonstrated implicitly via M2 picker clicks. one-doc-compare KeyDifferencesPanel without M5 still shows diffs + severity badges; just no per-row button. Saves ~0.5d.

**Deeper cut:** + M4 GCSFileBrowser. Picker alone (M2) covers the "demo without uploading" use case. Sidebar bucket library = library-browsing polish, ships v6.5. Saves another ~1d.

**Demo-blocker cut:** + M2 picker — keep M1 schema + M3 greeting + M4 sidebar bucket. Greeting + sidebar GCSFileBrowser cover the minimum viable "user can demo without uploading" flow.

## Locked decisions (per Mark 2026-06-09)

- **Q1 thumbnails**: generic doc-icon fallback in v1; auto-rendered thumbnails defer to v6.5
- **Q4 action-name convention**: `<verb>_<entity>` documented as recommended pattern in `SKILL_AUTHOR_GUIDE.md`; not enforced
- **Q5 tenant placeholders in greeting**: deferred to v6.5

## Bucket-IAM blocker

`gs://aitana-examples-public/` may need creating + a one-time `allUsers:roles/storage.objectViewer` grant. Sprint JSON `open_questions_for_mark` field captures this. Surface to Mark at M1 kickoff:

```bash
# If bucket missing:
gsutil mb gs://aitana-examples-public/
gsutil iam ch allUsers:objectViewer gs://aitana-examples-public/
```

Schema work proceeds in parallel; only the 5-PPA-upload sub-task blocks until bucket exists.

## Risks

| Risk | Mitigation | Severity |
|---|---|---|
| Examples bucket doesn't exist | Surface to Mark at M1 kickoff; schema work continues until bucket lands | medium |
| Parallel collision with 4.2 M4 CLI | Different subcommand groups (`tenant`/`docs` vs `examples`/`bucket`); merge surface is 1 line in cli.py | low |
| chrome-devtools MCP locked at verify | curl-based HTML grep fallback (proven during M3.5) | low |
| Thu spills into Fri AM | Cut-line documented (drop M5 first, then M4, then M2) | low |
| Backend allowlist refresh on skill push | v1 reads at startup; restart-to-refresh; revisit in v6.5 | low |

## Execution

Per Mark's request 2026-06-09: continuous execution through M6 verify + commit. Pause only at end of sprint OR to surface the bucket-IAM blocker if it needs Mark's hands. Sprint-executor handles loop.
