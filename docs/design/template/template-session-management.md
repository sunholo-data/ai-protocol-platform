# Template Session Management Fixes

**Status**: ✅ #26 shipped in platform (audit-confirmed 2026-06-05) — **template sync pending**. #27 (bootstrap endpoint) **still planned**.  
**Priority**: P1  
**Estimated**: 1d planned; #26 done, #27 remains  
**Scope**: Backend  
**Dependencies**: None  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-05  
**Source items**: #26 #27 (CPH Uni AIPLA upstream feedback)

## Implementation Status (audit 2026-06-05)

| Item | Status | Files |
|------|--------|-------|
| **#26** `GET /api/sessions/{id}/state` uses `skill_id` not `APP_NAME` | ✅ Shipped | [backend/protocols/sessions_route.py:260,300](../../../backend/protocols/sessions_route.py#L260) — both call sites pass `app_name=APP_NAME` |
| **#27** `ChatSessionIndex` created lazily → 404 on pre-first-turn iframe pushes | ⏳ Still planned | Needs new `POST /api/sessions/{id}/bootstrap` endpoint that pre-creates the `ChatSessionIndex` Firestore row AND the ADK in-memory session. Frontend calls it fire-and-forget when `useSkillAgent` first sees a session id |

## Problem Statement

Two session-management bugs silently break real workflows:

**Item #26 — Wrong `app_name` in `GET /api/sessions/{id}/state`**

`backend/protocols/sessions_route.py` line 299 passes `app_name=idx.skill_id` to
`session_service.get_session(...)`. The canonical `app_name` is the `APP_NAME` constant
(`"aitana_platform"`), not the skill ID. The sibling POST in `iframe_context_routes.py`
had the same bug and was fixed; the GET was missed.

Result: `aiplatform sessions inspect --mcp-context <id>` always returns `{}` because the
lookup uses the wrong key. Anyone using the CLI or API to debug iframe-context state gets
a false empty response.

The tests for this endpoint used `MagicMock` session services that return a result
regardless of args — a mock that doesn't care about args can't catch a wrong-arg bug.

**Item #27 — `ChatSessionIndex` created lazily — iframe pushes pre-first-turn always 404**

`make_session_tracker` in `backend/adk/callbacks.py` line 487 creates the Firestore
`ChatSessionIndex` document on the first agent turn. Until then the index doesn't exist.

AIPLA's workspace surfaces (BoldkastSimFrame, ProgressChecklist) push `iframe-context`
the moment the student interacts with the UI — before sending any chat message. The
`/api/sessions/{id}/iframe-context` POST route calls `_require_session` which looks up
the index in Firestore, finds nothing, and returns 404. Six consecutive 404s were
observed in a real student session before the first chat turn.

The catch-up effect (retry on next interaction) means the agent never sees the student's
first iframe interactions — e.g., "student opened sim, revealed y_max, then asked for
help" is lost.

The same race exists in the template for any MCP App that fires `ui/update-model-context`
before the first agent turn (which `@mcp-ui/client`'s `AppRenderer` does on iframe load).

**Impact:**

- Item #26: CLI session debugging is silently broken. Anyone using `aiplatform sessions
  inspect` to debug iframe-context pushes gets an empty dict and wastes time chasing a
  false lead.
- Item #27: First-turn context from any iframe surface is silently dropped. The agent
  gives pedagogically wrong answers ("please share your values") when it already has them.

## Goals

**Primary Goal:** Session state lookups use the correct `app_name`; session indices exist
before the first agent turn so pre-turn iframe pushes are not 404'd.

**Success Metrics:**
- `GET /api/sessions/{id}/state` returns the correct MCP context state for a session that had iframe pushes.
- `POST /api/sessions/{id}/bootstrap` pre-creates both the Firestore index and the ADK in-memory session.
- Frontend calls bootstrap fire-and-forget when `useSkillAgent` first sees a session ID.
- Non-mock test exercises a real `InMemorySessionService` for the state GET endpoint.

**Non-Goals:**
- Changing the overall session model or `ChatSessionIndex` schema.
- Persistent ADK sessions (the ADK session is still in-memory; bootstrap just pre-creates the Firestore mirror).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Eliminates 404 retry loop; first iframe push lands immediately |
| 2 | EARNED TRUST | +1 | Agent has correct context; doesn't ask for values it already has |
| 3 | SKILLS, NOT FEATURES | 0 | |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Correct context = better model outputs |
| 5 | GRACEFUL DEGRADATION | 0 | |
| 6 | PROTOCOL OVER CUSTOM | 0 | |
| 7 | API FIRST | +1 | State GET returns correct data; CLI works |
| 8 | OBSERVABLE BY DEFAULT | +1 | Session debug flow now returns real data |
| 9 | SECURE BY CONSTRUCTION | 0 | |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+5** | Meets threshold |

## Design

### Item #26 — One-line fix + non-mock test

**File:** `backend/protocols/sessions_route.py`

```python
# Before
session = await session_service.get_session(
    app_name=idx.skill_id,   # BUG: skill_id is not the app_name
    user_id=idx.user_id,
    session_id=session_id,
)

# After
from backend.adk.agui import APP_NAME

session = await session_service.get_session(
    app_name=APP_NAME,       # "aitana_platform" — the canonical constant
    user_id=idx.user_id,
    session_id=session_id,
)
```

Add a comment referencing the sibling POST fix (to prevent the same mistake recurring):

```python
# NOTE: app_name must be APP_NAME, not skill_id. Same fix as iframe_context_routes.py.
```

**Non-mock test:**

```python
# backend/tests/api_tests/test_session_state.py
async def test_get_session_state_uses_correct_app_name():
    """Verify the GET uses APP_NAME, not skill_id."""
    service = InMemorySessionService()
    session = await service.create_session(
        app_name=APP_NAME, user_id="user-1", session_id="sess-1"
    )
    await service.add_state(session, {"mcp_app_context.boldkast.tool": {"x": 1}})

    # Index pointing at a different skill_id
    index = ChatSessionIndex(session_id="sess-1", skill_id="different-skill", user_id="user-1")

    response = await client.get(
        "/api/sessions/sess-1/state",
        headers={"Authorization": "..."},
    )
    assert response.status_code == 200
    assert response.json()["mcp_app_context"]["boldkast"]["tool"]["x"] == 1
```

The key: the test wires a real `InMemorySessionService` into the route so the
`app_name` argument is actually checked against stored sessions.

### Item #27 — Session bootstrap endpoint

**File:** `backend/protocols/session_bootstrap_routes.py` (new file)

```python
# POST /api/sessions/{session_id}/bootstrap
# Pre-creates ChatSessionIndex in Firestore + ADK in-memory session.
# Called fire-and-forget by the frontend when useSkillAgent first sees a session_id.

@router.post("/api/sessions/{session_id}/bootstrap", status_code=204)
async def bootstrap_session(
    session_id: str,
    request: BootstrapRequest,  # skill_id, group_id (optional)
    current_user: User = Depends(get_current_user),
    db: FirestoreClient = Depends(get_db),
    session_service: BaseSessionService = Depends(get_session_service),
):
    # 1. Create or skip ChatSessionIndex in Firestore (idempotent)
    existing = await db.get_document("chat_sessions", session_id)
    if not existing:
        await db.set_document("chat_sessions", session_id, ChatSessionIndex(
            session_id=session_id,
            skill_id=request.skill_id,
            user_id=current_user.uid,
            created_at=datetime.utcnow().isoformat(),
        ).model_dump())

    # 2. Pre-create ADK in-memory session (idempotent)
    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=current_user.uid,
            session_id=session_id,
        )
    except SessionAlreadyExistsError:
        pass   # already exists — fine

    return Response(status_code=204)
```

The existing `before_agent_callback` in `callbacks.py` stays as a backstop — if the
bootstrap wasn't called (old clients, or race condition), the index is created on the
first agent turn as before.

**Frontend call site:**

```ts
// frontend/src/hooks/useSkillAgent.ts
useEffect(() => {
  if (!sessionId || !skillId) return;
  // Fire-and-forget: pre-create session before any iframe push or first turn
  fetchWithAuth(`/api/proxy/api/sessions/${sessionId}/bootstrap`, {
    method: "POST",
    body: JSON.stringify({ skill_id: skillId }),
  }).catch(() => {});  // errors are non-fatal; before_agent_callback is the backstop
}, [sessionId, skillId]);
```

Call this as early as possible — immediately when `useSkillAgent` receives its first
`sessionId`, before the user has typed anything.

**Tests:**

```python
# backend/tests/api_tests/test_session_bootstrap.py
async def test_bootstrap_creates_index_and_session():
    response = await client.post(
        "/api/sessions/sess-123/bootstrap",
        json={"skill_id": "problem-set-hints"},
        headers=auth_headers,
    )
    assert response.status_code == 204

    # Firestore index created
    doc = await db.get_document("chat_sessions", "sess-123")
    assert doc["session_id"] == "sess-123"

    # ADK session created
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id="sess-123"
    )
    assert session is not None

async def test_bootstrap_is_idempotent():
    for _ in range(3):
        response = await client.post("/api/sessions/sess-123/bootstrap", ...)
        assert response.status_code == 204   # no error on repeat calls
```

### CLI Surface

Extend `aiplatform sessions` with a `bootstrap` subcommand for manual testing:

```bash
aiplatform sessions bootstrap <session-id> --skill <skill-id>
# → POST /api/sessions/<id>/bootstrap
# → 204 No Content
```

This makes it easy to reproduce the "iframe before first turn" scenario in local dev
without needing a browser.

## Implementation Plan

| Step | File(s) | Effort |
|------|---------|--------|
| 1 | Fix `app_name=idx.skill_id` → `APP_NAME` in `sessions_route.py` (#26) | 0.5h |
| 2 | Write non-mock test for state GET using real `InMemorySessionService` | 1h |
| 3 | Write `session_bootstrap_routes.py` + wire into FastAPI app (#27) | 2h |
| 4 | Add `useEffect` bootstrap call to `useSkillAgent.ts` | 1h |
| 5 | Write `test_session_bootstrap.py` (idempotency + index + session creation) | 1.5h |
| 6 | Add `aiplatform sessions bootstrap` CLI subcommand | 1h |
| 7 | Manual smoke: iframe push before first turn; assert context present in agent reply | 0.5h |

**Total: ~7.5h ≈ 1d**

## Testing Strategy

- **Unit (`test_session_state.py`):** Real `InMemorySessionService`; assert correct state
  returned when `app_name=APP_NAME` is used.
- **Integration (`test_session_bootstrap.py`):** Bootstrap → then POST iframe-context →
  assert 200 (not 404); then verify session state contains iframe payload.
- **Frontend (`useSkillAgent.test.ts`):** Assert bootstrap fetch is called when `sessionId`
  changes from null to a value.

## Success Criteria

- [ ] `GET /api/sessions/{id}/state` returns correct MCP context (not `{}`).
- [ ] `POST /api/sessions/{id}/bootstrap` returns 204; subsequent iframe-context POSTs return 200.
- [ ] Repeated bootstrap calls are idempotent (no error, no duplicate Firestore docs).
- [ ] `useSkillAgent` calls bootstrap fire-and-forget on first `sessionId`.
- [ ] `aiplatform sessions bootstrap` CLI command works against local and deployed backend.
- [ ] All tests use real `InMemorySessionService` (no mocks that ignore `app_name`).

## Related Documents

- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — session debugging tools
- [mcp-app-update-model-context.md](../../v6.1.0/implemented/mcp-app-update-model-context.md)
- [SEQUENCE.md](SEQUENCE.md)
