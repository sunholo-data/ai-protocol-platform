---
name: sprint-executor
description: Execute approved sprint plans with test-driven development, continuous linting, progress tracking, and pause points. Supports parallel milestone execution via Task sub-agents. Use when user says "execute sprint", "start sprint", "begin implementation", "run the sprint plan", or wants to implement an approved sprint plan. Also use when resuming a paused sprint across sessions.
---

# Sprint Executor

Execute an approved sprint plan with continuous progress tracking, testing, and documentation updates. Supports **parallel execution** of independent milestones using Task sub-agents.

## Quick Start

**Sequential execution (default):**
```bash
# User says: "Execute the sprint plan for Slack integration"
# This skill will:
# 1. Validate prerequisites (tests pass, linting clean)
# 2. Create TodoWrite tasks for all milestones
# 3. Execute each milestone with test-driven development
# 4. Run checkpoint after each milestone (tests + lint + typecheck)
# 5. Pause after each milestone for user review
```

**Parallel execution (for independent milestones):**
```bash
# User says: "Execute sprint in parallel"
# This skill will:
# 1. Analyze milestone dependencies
# 2. Group independent milestones for parallel execution
# 3. Spawn Task sub-agents per milestone (branch, TDD, implement, commit)
# 4. Act as integration agent (merge branches, run full test suite)
```

## When to Use This Skill

- User says "execute sprint", "start sprint", "begin implementation"
- User has an approved sprint plan ready to implement
- User wants to resume a paused sprint from a previous session

**Choose parallel** when: 3+ milestones with independent work, different files
**Choose sequential** when: milestones have strict dependencies, shared files, or sprint is small

## Core Principles

1. **Test-Driven**: All code must pass tests before moving to next milestone
2. **Lint-Clean**: All code must pass linting and typecheck before proceeding
3. **Document as You Go**: Update sprint plan progressively
4. **Pause for Breath**: Stop at natural breakpoints for user review
5. **Track Everything**: Use TodoWrite for visible progress
6. **Parallelize When Possible**: Independent milestones run as concurrent Task sub-agents

## Multi-Session Continuity

Sprint execution can span multiple Claude Code sessions using JSON progress files.

- **Session Startup**: Every continuing session starts with `scripts/session_start.sh`
- **Progress Tracking**: JSON file at `.claude/state/sprints/sprint_<id>.json`
- **Pause and Resume**: Status saved; next session picks up where you left off
- **Constrained Modification**: Only `passes`, `completed`, `notes`, `actual_loc` change during execution

## Available Scripts

### `scripts/session_start.sh <sprint_id>`
Resume sprint across sessions. **Run ALWAYS at session start for continuing sprints.**

### `scripts/validate_prerequisites.sh`
Pre-flight checks before starting sprint execution.

Checks: git status, branch, frontend tests + lint, backend tests.

### `scripts/milestone_checkpoint.sh <milestone_name> [sprint_id]`
Post-milestone quality gate. **Run after completing each milestone.**

Checks: `npm run quality:check:fast`, `cd backend && pytest`, git diff, file sizes.

### `scripts/finalize_sprint.sh <sprint_id>`
Complete sprint: move design doc to implemented, update JSON status.

## Execution Flow

### Phase 0: Session Resumption (continuing sprints)

```bash
.claude/skills/sprint-executor/scripts/session_start.sh <sprint-id>
```
Prints "Here's where we left off" summary. Then skip to Phase 2.

### Phase 1: Initialize Sprint (first session only)

1. **Read Sprint Plan** - Parse markdown + load JSON progress file
2. **Check Model Assignment** - Read the plan's "Model Assignment" table
   (rubric: `.claude/skills/sprint-planner/resources/model-assignment.md`).
   If the current session's model differs from the assignment for the next
   milestone, tell the user and ask them to switch (`/model <id>`) before
   proceeding — never silently run a milestone assigned to a more capable
   model on a lighter one. The assignment exists because subtle milestones
   (streaming semantics, security gates) were scored as needing first-shot
   correctness; overriding it is the user's call, not yours. If the plan has
   no Model Assignment table (pre-rubric plans), note it and continue.
3. **Validate Prerequisites** - Run `scripts/validate_prerequisites.sh`
4. **Create Todo List** - Use TodoWrite to track all milestones
5. **Initial Status** - Mark sprint as in_progress in JSON

Re-check the model assignment at every milestone boundary (including session
resumes via `session_start.sh`) — assignments can change mid-sprint between
a Fable-assigned core and Opus-assigned plumbing.

### Phase 2: Choose Execution Mode

**Sequential (Phase 2A):** Milestones form a dependency chain
**Parallel (Phase 2B):** 2+ milestones are independent

### Phase 2A: Sequential Execution

For each milestone:

1. **Pre-Implementation** - Mark milestone in_progress in TodoWrite
2. **Write Failing Tests First** - TDD: write tests that capture expected behavior, verify they fail
3. **Implement** - Write code to make tests pass
4. **Verify Quality** - Run `scripts/milestone_checkpoint.sh <name>`
   ```bash
   # Quick check (< 30s)
   npm run quality:check:fast
   # Backend
   cd backend && pytest tests/ -m "not slow" -v --tb=short
   ```
5. **Update Sprint JSON** - Set `passes: true/false`, `completed` timestamp, `notes`
6. **Commit** - Git commit with milestone reference
7. **Pause for Breath** - Show progress, ask user if ready to continue

### Phase 2B: Parallel Milestone Execution

#### Step 1: Dependency Analysis
Build dependency graph from sprint JSON. Group into parallelizable waves:
```
Wave 1: [M1-frontend, M2-backend]  <- no dependencies, different files
Wave 2: [M3-fullstack]             <- depends on Wave 1
```

#### Step 2: Spawn Sub-Agents
For each wave, spawn one Task sub-agent per milestone **in a single message**.
Set each sub-agent's `model` param from the sprint plan's Model Assignment
table (procedural milestones on sonnet/haiku, subtle cores on the assigned
heavy model).

Each sub-agent:
- Works on its own branch (`sprint/<milestone-slug>`)
- Writes failing tests FIRST
- Implements until tests pass
- Runs quality checks
- Commits with milestone reference
- Reports back with MILESTONE_REPORT

#### Step 3: Collect Results
Parse sub-agent reports. If any failed, do NOT proceed to next wave.

#### Step 4: Repeat for Each Wave
Waves execute sequentially; milestones within a wave run in parallel.

### Phase 3: Integration (after parallel execution)

1. **Create integration branch** from dev
2. **Merge milestone branches** in dependency order
3. **Run full test suite**:
   ```bash
   npm run docker:check              # Frontend: lint + typecheck + test + build
   cd backend && pytest tests/ -v    # Backend: full suite
   ```
4. **Resolve conflicts** if any, preserving both milestones' functionality
5. **Update sprint JSON** with final status

### Phase 4: Finalize Sprint

1. **Final Commit** with sprint summary
2. **Run finalize script**:
   ```bash
   .claude/skills/sprint-executor/scripts/finalize_sprint.sh <sprint-id>
   ```
3. **Summary Report** - Compare planned vs actual (LOC, time)

### Phase 5: Hand Off to Sprint Evaluator

After finalizing, invoke the `sprint-evaluator` skill for independent quality assessment.

## Quality Commands Reference

```bash
# Quick frontend validation (< 30s)
npm run quality:check:fast        # lint + typecheck

# Full frontend validation
npm run docker:check              # lint + typecheck + test + build

# Backend validation
cd backend && source .venv/bin/activate
pytest tests/ -m "not slow" -v --tb=short   # Fast tests
pytest tests/ -v --tb=short                  # Full suite

# Individual checks
npm run lint
npx tsc --noEmit
npm run test:run
npm run build
```

## File Size Guidelines

Soft cap: 800 lines for any file. Past that, consider splitting by responsibility. No hard per-type limits.

## Error Handling

- **Tests fail**: Show output, fix before proceeding. Never skip.
- **Lint/typecheck fail**: Show output, fix immediately.
- **Implementation unclear**: Ask user for clarification.
- **Velocity much lower than expected**: Pause after 2-3 milestones, reassess scope.
- **Sub-agent fails (parallel)**: Report failure, options: retry, fix manually, or skip.
- **Merge conflicts (parallel)**: Resolve preserving both milestones, run tests after.

## Prerequisites

- Working directory clean (or only sprint-related changes)
- Current branch `dev` (or sprint feature branch)
- All existing tests pass
- Sprint plan approved and documented
- JSON progress file created and populated by sprint-planner

## Notes

- This skill is long-running - expect it to take hours or days
- Pause points are built in at each milestone
- Sprint plan is source of truth, but reality may require adjustments
- Git commits create a reversible audit trail
- Test-driven development is non-negotiable
- Multi-session continuity via JSON state tracking
