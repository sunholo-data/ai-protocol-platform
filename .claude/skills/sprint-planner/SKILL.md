---
name: sprint-planner
description: Analyze design docs, calculate development velocity, and create realistic sprint plans with day-by-day breakdowns for the Aitana Labs platform. Use when user asks to "plan sprint", "create sprint plan", "estimate timeline", "break down work", or wants to plan implementation of a design doc. Also triggers when user says "how long will this take" for a feature.
---

# Sprint Planner

Create comprehensive, data-driven sprint plans by analyzing design documentation, current implementation status, and recent development velocity.

## Quick Start

```bash
# User says: "Plan a sprint for the Slack integration design doc"
# This skill will:
# 1. Read the design doc
# 2. Analyze recent velocity from git history
# 3. Review current implementation status
# 4. Propose realistic milestones with LOC estimates
# 5. Create day-by-day task breakdown
# 6. Create JSON progress file for multi-session tracking
```

## When to Use This Skill

Invoke this skill when:
- User says "plan sprint", "create sprint plan", "plan next phase"
- User asks to estimate timeline for a feature or design doc
- User wants to know how long implementation will take
- User needs to prioritize work for upcoming development

## Available Scripts

### `scripts/analyze_velocity.sh [days]`
Analyze recent development velocity from git history.

```bash
# Analyze last 7 days (default)
.claude/skills/sprint-planner/scripts/analyze_velocity.sh

# Analyze last 14 days
.claude/skills/sprint-planner/scripts/analyze_velocity.sh 14
```

### `scripts/create_sprint_json.sh <sprint_id> <sprint_plan_md> [design_doc_md]`
Create structured JSON progress file for multi-session sprint execution.

```bash
.claude/skills/sprint-planner/scripts/create_sprint_json.sh \
  "SLACK-INT" \
  "docs/design/v6.0.0/slack-integration-sprint.md" \
  "docs/design/v6.0.0/slack-integration.md"
```

Creates `.claude/state/sprints/sprint_<id>.json` for session resumption.

## Sprint Planning Workflow

### 1. Read and Analyze Design Document

**Input**: Path to design doc (e.g., `docs/design/v6.0.0/feature.md`)

**Extract:**
- Implementation phases and tasks
- LOC estimates per phase
- Frontend vs backend scope
- Dependencies between tasks
- Success criteria / acceptance tests

### 2. Review Current Implementation Status

```bash
# Recent commits and velocity
.claude/skills/sprint-planner/scripts/analyze_velocity.sh

# Check what exists already
npm run quality:check:fast  # Frontend health
cd backend && pytest tests/ -m "not slow" --tb=line -q  # Backend health
```

### 3. Identify Work Breakdown

For each milestone, identify:
- **Scope**: `frontend`, `backend`, or `fullstack`
- **Dependencies**: What blocks what
- **Estimated LOC**: Implementation + tests
- **Priority**: Critical path vs nice-to-have
- **Risk**: Complexity, unknowns, external dependencies

### 4. Propose Sprint Plan

Use the template at `templates/sprint-plan.md`. Include:

- **Sprint Summary**: Goal, duration, key deliverables
- **Milestone Breakdown**: Each milestone with tasks, criteria, risks
- **Model Assignment**: Which Claude model runs each stage (see step 4a)
- **Day-by-Day Tasks**: Concrete daily goals (if sprint < 1 week)
- **Success Metrics**: Test coverage, quality gates

### 4a. Assign Models per Stage (MANDATORY)

Read [resources/model-assignment.md](resources/model-assignment.md) and score
each milestone on subtlety / horizon / spec-completeness, then fill the
**Model Assignment** table in the sprint plan: one row per stage
(planning / execution per milestone / evaluation / sub-agents) with model id
and a one-line why. The deciding factors are behavior fit and turn latency,
not cost — a subtle streaming or security-critical milestone belongs on the
most capable model; mechanical ports and config flags don't. Executor and
evaluator sessions read this table and honor it, so an unfilled table means
every later session re-derives the choice. Verify model ids against
`/claude-api` rather than memory.

### 5. Present for Feedback

Show the user:
- Proposed milestones with estimates
- Assumptions made
- Areas needing input
- Realistic timeline based on actual velocity

### 6. Finalize and Create JSON

Once approved:

```bash
# Create sprint plan markdown (save alongside design doc)
# Then create JSON progress file for multi-session execution
.claude/skills/sprint-planner/scripts/create_sprint_json.sh \
  "<sprint-id>" \
  "docs/design/YYYY-MM/<sprint-id>-sprint.md" \
  "docs/design/YYYY-MM/<feature>-design.md"
```

### 7. Populate JSON with Real Milestones

The script creates a template JSON. **You MUST populate it with real data before handing off to sprint-executor.**

Edit `.claude/state/sprints/sprint_<id>.json`:
- Replace placeholder features with real milestones
- Set real acceptance criteria (not "Criterion 1")
- Update velocity estimates to match sprint plan
- Ensure at least 2 milestones defined

### 8. Hand Off to Sprint Executor

After creating an approved sprint plan, invoke the `sprint-executor` skill to begin implementation. The executor reads the JSON progress file and sprint plan markdown.

## Multi-Session Continuity

Sprint plans create JSON progress files that enable sprints to span multiple Claude Code sessions. The sprint-executor uses these files to resume work from where it left off.

**JSON file location**: `.claude/state/sprints/sprint_<id>.json`

**Schema**: See sprint-executor `resources/milestone_checklist.md` for JSON structure.

## Best Practices

### 1. Be Conservative with Estimates
- Use actual velocity from recent git history
- Add 20-30% buffer for unknowns
- Don't promise more than recent velocity suggests

### 2. Scope Milestones by Stack
- Tag each milestone as `frontend`, `backend`, or `fullstack`
- Independent frontend/backend milestones can run in parallel
- Fullstack milestones are harder to parallelize

### 3. Make Tasks Concrete
- "Write Slack webhook handler (~80 LOC) + 10 test cases" is concrete
- "Implement Slack integration" is too vague
- Each task should be achievable in 1 day or less

### 4. Plan for Testing
- Frontend: Vitest + React Testing Library
- Backend: pytest with fixtures
- Test LOC is usually 30-50% of implementation LOC
- Include test writing in timeline estimates

### 5. Consider Quality Gates
- After each milestone: `npm run quality:check:fast`
- After all milestones: `npm run docker:check`
- Backend: `cd backend && pytest tests/ -v --tb=short`

### 6. Account for Frontend/Backend Split
- Frontend commands run from repo root
- Backend commands run from `backend/`
- API changes need both sides coordinated
- Use `/api/proxy` pattern for frontend → backend calls

## Notes

- Sprint plans should be realistic, not aspirational
- Use actual data (velocity, LOC counts) over guesses
- Update design docs as reality diverges from plan
- Don't commit plan until approved by user
