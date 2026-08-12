# Sprint Plan: [Sprint ID] - [Feature Name]

## Summary
[1-2 sentence goal describing what this sprint accomplishes]

**Duration:** X days
**Scope:** Frontend | Backend | Fullstack
**Dependencies:** [List any blocking items]
**Risk Level:** Low / Medium / High
**Design Doc:** [Link to design doc]

## Current Status Analysis

### Recent Velocity
- Average LOC/day from recent work: [N]
- Recent milestone completion rate: [X/Y]
- Estimated capacity for this sprint: [LOC]

### Existing Implementation
- [What already exists that we build on]
- [Current test coverage in affected areas]

## Proposed Milestones

### Milestone 1: [Name]
**Scope:** frontend | backend | fullstack
**Goal:** [What this milestone achieves]
**Estimated:** [LOC] implementation + [LOC] tests = [total LOC]
**Duration:** [days]

**Tasks:**
- [ ] [Specific actionable task] (~LOC)
- [ ] [Specific actionable task] (~LOC)
- [ ] [Write tests for above] (~LOC)

**Files to Create/Modify:**
- `src/components/New.tsx` (new, ~LOC)
- `backend/service.py` (modify, ~LOC delta)

**Acceptance Criteria:**
- [ ] [Measurable, testable criterion]
- [ ] [Measurable, testable criterion]
- [ ] All tests passing
- [ ] Lint and typecheck clean

**Risks:**
- [Risk] - Mitigation: [approach]

### Milestone 2: [Name]
[Repeat structure]

### Milestone 3: [Name]
[Repeat structure]

## Model Assignment

<!-- See .claude/skills/sprint-planner/resources/model-assignment.md for the rubric.
     Executor and evaluator sessions read this table — keep it filled. -->

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | [claude-opus-4-8] | [e.g. decomposition + interactive iteration] |
| Execute Milestone 1 | [model id] | [subtlety/horizon rationale] |
| Execute Milestone 2 | [model id] | [rationale] |
| Evaluation (all rounds) | [claude-opus-4-8 + report-everything] | [prefer a different model than the executor's] |
| Sub-agents | [claude-sonnet-4-6 / claude-haiku-4-5] | [procedural fan-out] |

## Day-by-Day Breakdown

### Day 1
- **Focus:** [Milestone 1 foundation]
- **Tasks:** [Specific tasks]
- **Checkpoint:** [What "done" looks like for the day]

### Day 2
- **Focus:** [Continue milestone 1 or start 2]
- **Tasks:** [Specific tasks]
- **Checkpoint:** [Definition of done]

[Continue for each day...]

## Quality Gates

After each milestone:
```bash
npm run quality:check:fast    # Lint + typecheck (< 30s)
cd backend && pytest tests/ -m "not slow" -v --tb=short
```

After all milestones:
```bash
npm run docker:check          # Full CI simulation
```

## Success Metrics
- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/`)
- [ ] Lint and typecheck clean
- [ ] Docker build succeeds
- [ ] [Feature-specific metrics]

## Dependencies
- [External dependency or blocking item]
- [Prerequisite that must exist first]

## Open Questions
- [Question requiring user input]
- [Decision not yet made]

## Notes
- [Assumptions, caveats, or context]
