# Sprint Evaluation Scoring Rubric

100-point scale. Pass threshold: 70 points. Hard fails cause automatic rejection.

## Scoring Categories

### 1. Frontend Tests Pass (10 points) -- HARD FAIL

```bash
npm run test:run
```

| Condition | Score |
|-----------|-------|
| All tests pass | 10 |
| 1-2 test failures (unrelated to sprint) | 5 |
| Any sprint-related test failure | **HARD FAIL** |

### 2. Backend Tests Pass (10 points) -- HARD FAIL

```bash
cd backend && source .venv/bin/activate && pytest tests/ -v --tb=short
```

| Condition | Score |
|-----------|-------|
| All tests pass | 10 |
| 1-2 test failures (unrelated to sprint) | 5 |
| Any sprint-related test failure | **HARD FAIL** |

### 3. Lint + Typecheck Clean (10 points)

```bash
npm run quality:check:fast  # lint + typecheck
```

| Condition | Score |
|-----------|-------|
| Zero errors | 10 |
| 1-3 warnings only | 8 |
| 1-5 errors | 5 |
| >5 errors | 0 |

### 4. Acceptance Criteria Met (30 points) -- HARD FAIL if <50%

From sprint JSON `features[].acceptance_criteria`:

| Condition | Score |
|-----------|-------|
| 100% criteria met | 30 |
| 80-99% criteria met | 24 |
| 60-79% criteria met | 18 |
| 50-59% criteria met | 15 |
| <50% criteria met | **HARD FAIL** |

**How to verify:**
- For each criterion, check if the implementation actually satisfies it
- File-related: verify files exist and contain expected code
- Test-related: verify test functions exist and pass
- Behavior-related: trace through code path to confirm

### 5. Code Quality (15 points)

| Aspect | Points | How to Assess |
|--------|--------|---------------|
| File size compliance | 5 | Components <300 lines, utils <200 lines |
| Code reuse | 5 | No duplicated logic, uses existing utilities |
| Security | 5 | No OWASP top 10 issues, proper input validation |

**Deductions:**
- -2 per oversized file
- -2 per duplicated code block (>10 lines)
- -5 for security vulnerability (XSS, injection, etc.)
- -2 for unused imports or dead code
- -2 per incomplete sprint JSON artifact (missing notes, timestamps)

### 6. Documentation (15 points)

| Aspect | Points | How to Assess |
|--------|--------|---------------|
| Design doc updated | 5 | Status reflects implementation state |
| Sprint JSON complete | 5 | All milestones have notes, timestamps, passes |
| Code comments where needed | 5 | Complex logic has inline explanation |

### 7. Design Fidelity (5 points)

| Condition | Score |
|-----------|-------|
| Implementation matches design exactly | 5 |
| Minor deviations with good reason | 3-4 |
| Significant deviations documented | 1-2 |
| Requirements silently dropped | 0 |

### 8. Axiom Alignment (5 points)

Verify the implementation did not violate axioms that scored +1 in the design doc's Axiom Alignment table.

| Condition | Score |
|-----------|-------|
| All +1 axioms upheld in implementation | 5 |
| 1 axiom regression (was +1, implementation conflicts) | 3 |
| 2+ axiom regressions | 0 |

**How to verify:**
- Read the design doc's Axiom Alignment table
- For each axiom scored +1, confirm the implementation supports it
- Check especially: INSTANT FEEL (no new blocking steps), EARNED TRUST (sources shown), API FIRST (no channel-specific business logic)
- If no design doc exists or has no axiom table, score 3 (neutral)

## Hard Fail Conditions

These cause automatic rejection regardless of total score:

1. **Frontend tests broken** -- Any test that was passing before the sprint now fails
2. **Backend tests broken** -- Any test that was passing before the sprint now fails
3. **<50% acceptance criteria met** -- Sprint didn't achieve its core goals

## Score Interpretation

| Score | Interpretation |
|-------|---------------|
| 90-100 | Excellent. Ship it. |
| 80-89 | Good. Minor polish recommended but shippable. |
| 70-79 | Acceptable. Passes threshold but has notable gaps. |
| 60-69 | Below threshold. Needs another round of work. |
| <60 | Significant issues. Major rework needed. |

## Evaluation Report Format

```json
{
  "sprint_id": "SLACK-INT",
  "evaluation_round": 1,
  "timestamp": "2026-04-09T10:00:00Z",
  "scores": {
    "frontend_tests": 10,
    "backend_tests": 10,
    "lint_typecheck": 8,
    "acceptance_criteria": 24,
    "code_quality": 12,
    "documentation": 10,
    "design_fidelity": 4,
    "axiom_alignment": 5
  },
  "total_score": 83,
  "result": "pass",
  "hard_fails": [],
  "issues": [
    {
      "category": "acceptance_criteria",
      "file": "src/components/Slack/SlackConnect.tsx",
      "issue": "OAuth flow not handling token refresh",
      "suggestion": "Add token refresh logic in useSlackAuth hook",
      "severity": "medium"
    }
  ],
  "summary": "Sprint passes with score 82/100. Minor gaps in acceptance criteria coverage."
}
```
