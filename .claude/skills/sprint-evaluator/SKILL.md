---
name: sprint-evaluator
description: Evaluate sprint implementations against design docs and acceptance criteria with concrete scoring rubric (100 points, 70 to pass). Provides actionable feedback loop on failure. Use when user says "evaluate sprint", "review implementation", "assess sprint quality", "grade the sprint", or after sprint-executor completes. Also triggers for "is this ready to ship" or "quality check the sprint".
---

# Sprint Evaluator

Independently evaluate a completed sprint implementation against its design doc, acceptance criteria, and quality standards. Based on the generator-evaluator architecture -- separating the agent doing the work from the agent judging it.

## Quick Start

```bash
# User says: "Evaluate the sprint SLACK-INT"
# This skill will:
# 1. Load design doc, sprint plan, and sprint JSON
# 2. Run automated quality checks (frontend + backend tests, lint, typecheck)
# 3. Verify each acceptance criterion from sprint JSON
# 4. Score implementation against concrete rubric (100 points)
# 5. PASS (score >= 70) or FAIL (send actionable feedback)
```

## When to Use This Skill

- Sprint-executor completes a sprint
- User says "evaluate sprint", "review implementation", "assess quality"
- User wants independent quality assessment before merge/deploy
- User asks "is this ready to ship?"

## Core Principles

1. **Independent Judge** -- Never evaluate your own work. This skill judges sprint-executor output
2. **Concrete Criteria** -- Score against measurable rubric, not subjective impressions
3. **Skeptical by Default** -- Tuned toward skepticism
4. **Actionable Feedback** -- Failed evaluations include specific files and suggestions
5. **Hard Thresholds** -- Tests broken or <50% criteria met = automatic rejection
6. **Bounded Iteration** -- Max 3 evaluation rounds before escalating to user

## Available Scripts

### `scripts/evaluate_sprint.sh <sprint_id>`
Run automated quality checks. Outputs test/lint/typecheck results.

### `scripts/check_acceptance_criteria.sh <sprint_id>`
Verify each acceptance criterion from sprint JSON. Per-criterion pass/fail.

### `scripts/generate_report.sh <sprint_id> <score> <result> <round>`
Create evaluation report JSON at `.claude/state/evaluations/`.

## Evaluation Phases

### Phase 1: Context Loading

1. Load sprint JSON from `.claude/state/sprints/sprint_<id>.json`
2. Read design doc (the original spec / "contract")
3. Read sprint plan (the implementation roadmap)
4. **Check the plan's "Model Assignment" table** (rubric:
   `.claude/skills/sprint-planner/resources/model-assignment.md`):
   - If the current session model differs from the evaluation assignment,
     tell the user before scoring (`/model <id>` to switch).
   - Independence extends to models: prefer evaluating with a *different*
     model than the one that executed the milestones — cross-model diversity
     catches blind spots self-review misses.
   - When evaluating on Opus-tier: it follows severity filters literally, so
     instruct yourself to report **every** finding with a confidence tag and
     filter downstream — never "only high-severity issues".
5. Review git diff of all changes
6. Check evaluation round (1, 2, or 3?)

### Phase 2: Automated Checks

```bash
.claude/skills/sprint-evaluator/scripts/evaluate_sprint.sh <sprint-id>
```

Runs:
- `npm run quality:check:fast` -- Lint + typecheck (HARD FAIL if broken)
- `npm run test:run` -- Frontend tests (HARD FAIL if broken)
- `cd backend && pytest tests/` -- Backend tests (HARD FAIL if broken)
- File size checks per CLAUDE.md guidelines

### Phase 3: Acceptance Criteria Verification

```bash
.claude/skills/sprint-evaluator/scripts/check_acceptance_criteria.sh <sprint-id>
```

For each feature in sprint JSON:
1. Read `acceptance_criteria` array
2. Verify `passes: true` for each feature
3. For file-related criteria, verify files exist
4. For test-related criteria, verify test functions exist
5. Score: `(criteria_met / total_criteria) * 30 points`

**HARD FAIL if fewer than 50% of acceptance criteria are met.**

### Phase 4: Design Fidelity Check

Compare implementation against design doc:
- Does the implementation match stated goals?
- Are architectural decisions consistent with design?
- Were any requirements silently dropped?
- Are there unexpected deviations from spec?

Score 0-10 based on fidelity to design intent.

### Phase 5: Documentation Completeness

Check:
- Design doc status updated (5 pts)
- Sprint JSON milestones all have notes (5 pts)
- Any new components/endpoints documented (5 pts)

### Phase 6: Scoring & Report

See [resources/scoring_rubric.md](resources/scoring_rubric.md) for full details.

| Category | Points | Hard Fail? |
|----------|--------|------------|
| Frontend Tests Pass | 10 | Yes |
| Backend Tests Pass | 10 | Yes |
| Lint + Typecheck Clean | 10 | No |
| Acceptance Criteria | 30 | Yes if <50% |
| Code Quality | 15 | No |
| Documentation | 15 | No |
| Design Fidelity | 10 | No |
| **Total** | **100** | **Pass: 70+** |

Generate report:
```bash
.claude/skills/sprint-evaluator/scripts/generate_report.sh <sprint-id> <score> pass|fail <round>
```

### Phase 7: Pass/Fail Decision

**On PASS (score >= 70, no hard fails):**
1. Move design doc to implemented (via design-doc-creator script)
2. Post congratulatory summary with score breakdown

**On FAIL (score < 70 or hard fail, round < 3):**
1. Generate specific, actionable feedback
2. List: file path, issue description, suggestion to fix
3. Invoke sprint-executor to address issues
4. Re-evaluate (increment round)

**On FAIL (round >= 3):**
1. Escalate to user for manual review
2. Post summary of all 3 rounds with score progression

## Feedback Loop

```
sprint-evaluator (Round 1)
    |
PASS? -> move design doc to implemented, done
FAIL? -> invoke sprint-executor with issues
            |
        sprint-evaluator (Round 2)
            |
        PASS? -> done
        FAIL? -> invoke sprint-executor (Round 3)
                    |
                PASS? -> done
                FAIL? -> escalate to user
```

## Notes

- Evaluation reports preserved at `.claude/state/evaluations/eval_<id>_round_<n>.json`
- The evaluator does NOT modify sprint JSON -- creates separate evaluation artifacts
- Hard fails cause immediate rejection regardless of total score
- Score progression across rounds is tracked for learning
- Evaluator is stateless per round
