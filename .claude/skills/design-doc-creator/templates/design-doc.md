# [Feature Name]

**Status**: Planned
**Priority**: P0 (High) | P1 (Medium) | P2 (Low)
**Estimated**: [e.g., 3 days, 1 week]
**Scope**: Frontend | Backend | Fullstack
**Dependencies**: None
**Created**: YYYY-MM-DD
**Last Updated**: YYYY-MM-DD

## Problem Statement

[What problem does this solve? Why is it needed?]

**Current State:**
- Pain point 1 (with metrics if available)
- Pain point 2
- Pain point 3

**Impact:**
- Who is affected? (users, developers, operations)
- How significant? (blocker, major friction, nice-to-have)

## Goals

**Primary Goal:** [One-sentence main objective with measurable outcome]

**Success Metrics:**
- Metric 1 (e.g., reduce load time from Xs to Ys)
- Metric 2 (e.g., support N concurrent users)
- Metric 3 (e.g., reduce error rate from X% to Y%)

**Non-Goals:**
- [What this feature explicitly does NOT try to solve]

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | | |
| 2 | EARNED TRUST | | |
| 3 | SKILLS, NOT FEATURES | | |
| 4 | RIGHT MODEL, RIGHT MOMENT | | |
| 5 | GRACEFUL DEGRADATION | | |
| 6 | PROTOCOL OVER CUSTOM | | |
| 7 | API FIRST | | |
| 8 | OBSERVABLE BY DEFAULT | | |
| 9 | SECURE BY CONSTRUCTION | | |
| 10 | THIN CLIENT, FAT PROTOCOL | | |
| | **Net Score** | **—** | Threshold: >= +4 |

**Conflict Justifications:**
- [If any axiom scored -1, explain why the tradeoff is acceptable for this feature]

## Design

### Overview

[High-level approach in 2-3 sentences]

### Frontend Changes

**New Components:**
- `src/components/FeatureName/` - [Description]

**Modified Components:**
- `src/components/Existing.tsx` - [What changes and why]

**State Management:**
- [New contexts, hooks, or state changes]

**UI/UX:**
- [Key user interactions and flows]

### Backend Changes

**New Endpoints:**
- `POST /vac/endpoint` - [Description, request/response format]

**Modified Endpoints:**
- `GET /vac/existing` - [What changes and why]

**New Services/Modules:**
- `backend/new_service.py` - [Description]

**Data Model Changes:**
- [Firestore collection changes, new fields, migrations]

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST   | /vac/new | New endpoint | No        |
| PUT    | /vac/existing | Modified | Yes - [details] |

### Architecture Diagram

```
[User] → [Frontend Component] → [/api/proxy] → [Backend Endpoint]
                                                       ↓
                                                 [Service Layer]
                                                       ↓
                                                 [Firestore/GCS]
```

## Implementation Plan

### Phase 1: [Foundation] (~X days)
- [ ] Task 1 - [Description] (~LOC estimate)
- [ ] Task 2 - [Description] (~LOC estimate)

### Phase 2: [Core Feature] (~X days)
- [ ] Task 3 - [Description] (~LOC estimate)
- [ ] Task 4 - [Description] (~LOC estimate)

### Phase 3: [Polish & Integration] (~X days)
- [ ] Task 5 - [Description] (~LOC estimate)
- [ ] Task 6 - [Description] (~LOC estimate)

## Migration & Rollout

**Database Migrations:**
- [New Firestore collections/fields needed]
- [Data backfill requirements]

**Feature Flags:**
- [How to gradually roll out]

**Rollback Plan:**
- [How to safely roll back if issues arise]

**Environment Variables:**
- [New env vars needed, for which environments]

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] Component unit tests for new components
- [ ] Hook tests for new custom hooks
- [ ] Integration tests for key user flows

### Backend Tests (pytest)
- [ ] API endpoint tests
- [ ] Service layer unit tests
- [ ] Integration tests with Firestore emulator

### Manual Testing
- [ ] [Key scenario 1]
- [ ] [Key scenario 2]
- [ ] [Edge case to verify]

## Security Considerations

- [Authentication/authorization requirements]
- [Data privacy implications]
- [Input validation needs]
- [OWASP considerations]

## Performance Considerations

- [Expected load/scale]
- [Caching strategy]
- [Bundle size impact (frontend)]
- [Response time targets]

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/`)
- [ ] Lint and typecheck clean (`npm run quality:check:fast`)
- [ ] Docker build succeeds (`npm run docker:check`)
- [ ] Documentation updated
- [ ] [Feature-specific acceptance criteria]
- [ ] [Feature-specific acceptance criteria]

## Open Questions

- [Question requiring input or clarification]
- [Decision that hasn't been made yet]

## Related Documents

- [Link to related design docs, issues, or PRs]
