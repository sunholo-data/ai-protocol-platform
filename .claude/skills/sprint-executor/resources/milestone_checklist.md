# Milestone Checklist

Step-by-step verification for each milestone during sprint execution.

## Before Starting a Milestone

- [ ] Previous milestone completed and checkpoint passed
- [ ] Sprint JSON updated with previous milestone status
- [ ] TodoWrite updated to show current milestone in_progress
- [ ] Understand acceptance criteria from sprint plan

## During Implementation

### Test-Driven Development
1. [ ] Write failing tests that capture expected behavior
2. [ ] Verify tests fail (`npm run test:run` / `pytest`)
3. [ ] Implement minimum code to make tests pass
4. [ ] Verify tests pass
5. [ ] Refactor if needed (tests still pass)

### Quality Checks (run frequently)
```bash
# Quick frontend check (< 30s)
npm run quality:check:fast

# Backend check
cd backend && pytest tests/ -m "not slow" -q --tb=line
```

### File Size
- Soft cap: 800 lines for any file. Past that, consider splitting by responsibility.
- No hard per-type limits — favour readable, coherent files over forced splits.

## After Completing a Milestone

1. [ ] Run milestone checkpoint script:
   ```bash
   .claude/skills/sprint-executor/scripts/milestone_checkpoint.sh <name> <sprint-id>
   ```
2. [ ] All tests passing (frontend + backend)
3. [ ] Lint and typecheck clean
4. [ ] No oversized files introduced
5. [ ] Git commit with milestone reference
6. [ ] Update sprint JSON:
   - Set `passes: true` (or `false` with explanation)
   - Set `completed: "<ISO timestamp>"`
   - Add `notes: "<summary of what was done>"`
   - Update `actual_loc` if tracked
7. [ ] Mark milestone complete in TodoWrite
8. [ ] Pause for user review

## Sprint JSON Schema

```json
{
  "sprint_id": "string",
  "created": "ISO 8601",
  "estimated_duration_days": "number",
  "design_doc": "string (path)",
  "sprint_plan": "string (path)",
  "features": [
    {
      "id": "string",
      "description": "string",
      "scope": "frontend | backend | fullstack",
      "estimated_loc": "number",
      "actual_loc": "number | null",
      "dependencies": ["string"],
      "acceptance_criteria": ["string"],
      "files_to_create": ["string"],
      "files_to_modify": ["string"],
      "passes": "null | true | false",
      "started": "ISO 8601 | null",
      "completed": "ISO 8601 | null",
      "notes": "string | null"
    }
  ],
  "velocity": {
    "target_loc_per_day": "number",
    "actual_loc_per_day": "number | null",
    "estimated_total_loc": "number",
    "actual_total_loc": "number | null",
    "estimated_days": "number",
    "actual_days": "number | null"
  },
  "github_issues": ["number"],
  "last_session": "ISO 8601 | null",
  "last_checkpoint": "string | null",
  "status": "not_started | in_progress | paused | completed"
}
```

### Constrained Modification Pattern

During execution, only these fields should change:
- `passes`: `null` → `true` or `false`
- `actual_loc`, `completed`, `notes`: progress tracking
- `started`: when milestone begins
- `last_session`, `last_checkpoint`, `status`: session tracking

Do NOT modify during execution:
- `description`, `acceptance_criteria` (prevents requirement drift)
- `estimated_loc` (preserves original estimate for retrospective)
- Do not remove features (prevents losing work)
- Do not add new features mid-sprint (add to backlog)
