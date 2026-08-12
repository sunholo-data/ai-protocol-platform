# Auth & Permissions

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 3 days
**Scope**: Backend + Frontend
**Dependencies**: [Skills Data Model](skills-data-model.md)
**Created**: 2026-04-10
**Last Updated**: 2026-04-21

## Problem Statement

v6 needs auth and permissions redesigned for skills (not assistants). v5's permission model works well but is tightly coupled to Sunholo's config system and the "assistant" concept. v6 needs:

1. **Firebase Auth middleware** — verify JWT tokens on every API request
2. **Skill access control** — who can use which skills (private/public/domain/specific)
3. **Tool permissions** — which tools a user/domain can invoke
4. **Owner permissions** — only skill owners can edit/delete their skills

**Current State:**
- v5 has working `tool_permissions.py` with per-user/domain wildcard support
- v5 Firebase auth works but is wired through Sunholo
- `backend/auth/` exists in v6 but is empty
- Frontend has no auth flow yet

**Impact:**
- Blocks all authenticated API endpoints
- Blocks skill CRUD (owner verification)
- Blocks tool execution (permission checks in `_before_tool` callback)

## Goals

**Primary Goal:** Implement auth middleware and permission checks that secure all API endpoints and tool executions without adding perceptible latency.

**Success Metrics:**
- All API endpoints require valid Firebase JWT (except health check and marketplace browse)
- Skill access enforced: private skills visible only to owner, domain skills to domain members
- Tool permissions checked in <5ms per tool invocation
- Zero auth bypasses in integration tests

**Non-Goals:**
- Role-based access control (RBAC) — keep it simple: owner vs. user
- API key auth for service-to-service calls (use SA identity)
- Multi-org/tenant auth (single-org for now)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Auth adds <5ms — negligible |
| 2 | EARNED TRUST | 0 | Not about factual claims |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to users |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Doesn't affect model selection |
| 5 | GRACEFUL DEGRADATION | 0 | Standard HTTP 401/403 responses |
| 6 | PROTOCOL OVER CUSTOM | 0 | Firebase Auth is standard, not a protocol decision |
| 7 | API FIRST | +1 | Auth middleware serves all channels uniformly |
| 8 | OBSERVABLE BY DEFAULT | +1 | Auth errors logged with uid, tool permission checks traced |
| 9 | SECURE BY CONSTRUCTION | +1 | **Core purpose**: middleware + callbacks + Firestore rules — architecture enforces security |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Auth verified server-side, frontend just passes Bearer token |
| | **Net Score** | **+4** | Threshold: >= +4 |

## Design

### Overview

Auth has three layers: (1) Firebase JWT verification middleware on FastAPI, (2) skill-level access control checked on every skill read, and (3) tool-level permissions checked in the ADK `before_tool` callback. Permissions are stored in Firestore and cached in-memory.

### Firebase Auth Middleware

```python
# backend/auth/firebase_auth.py

from firebase_admin import auth as firebase_auth
from fastapi import Depends, HTTPException, Request

async def get_current_user(request: Request) -> User:
    """Extract and verify Firebase JWT from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    
    token = auth_header.split("Bearer ")[1]
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    
    return User(
        uid=decoded["uid"],
        email=decoded.get("email", ""),
        domain=decoded.get("email", "").split("@")[1] if "@" in decoded.get("email", "") else "",
    )


class User(BaseModel):
    uid: str
    email: str
    domain: str
```

**Public endpoints (no auth required):**
- `GET /health` — health check
- `GET /api/skills/marketplace` — browse public skills (read-only)
- `GET /.well-known/agent.json` — A2A agent card

**All other endpoints require `Depends(get_current_user)`.**

### Skill Access Control

**Canonical model:** see [resource-access-control.md](../resource-access-control.md) for the full `AccessControl` + `AccessContext` design. Skills reuse the same schema and evaluator as buckets, folders, and chat sessions — there is exactly **one** access-control code path in v6. The snippets below are the skill-specific wiring; the evaluator lives in `AccessContext.can_access()`.

Primary enforcement is at the Firestore rules layer via `canAccessResource()`. The API layer is a safety net that uses the request-scoped `AccessContext`:

```python
# backend/auth/access_context.py — methods on AccessContext (request-scoped)

def can_access_skill(self, skill: SkillConfig) -> bool:
    """Check if user can access (use) a skill."""
    return can_access(skill.accessControl, self, skill.ownerId)

def is_skill_owner(self, skill: SkillConfig) -> bool:
    """Check if user owns a skill (can edit/delete)."""
    return self.uid == skill.ownerId
```

The evaluator covers all five access types — `public | private | domain | specific | tagged`. The `tagged` variant matches `user.groupTags` (Firebase JWT custom claim) against `accessControl.tags`, and is the B2B team-sharing primitive (same one used by chat-history and buckets). See [resource-access-control.md](resource-access-control.md#the-accesscontrol-schema-already-exists--reuse-verbatim) for the schema and [resource-access-control.md](resource-access-control.md#request-scoped-accesscontext-the-latency-win) for the evaluator body.

### Tool Permissions

Ported from v5's `tool_permissions.py`. Per-user and per-domain tool access with wildcard support.

```
# Firestore: tool_permissions/{userId_or_domain}
{
  "allowed_tools": ["ai_search", "file_browser", "google_search"],
  "denied_tools": [],
  "wildcard": false   # true = all tools allowed
}
```

```python
# backend/auth/permissions.py

# In-memory cache (refreshed every 60s)
_permissions_cache: dict[str, ToolPermissions] = {}

async def can_use_tool(user: User, tool_name: str) -> bool:
    """Check if user/domain can use a specific tool."""
    # Check user-specific permissions first
    user_perms = await _get_permissions(user.uid)
    if user_perms:
        if user_perms.wildcard:
            return tool_name not in user_perms.denied_tools
        return tool_name in user_perms.allowed_tools
    
    # Fall back to domain permissions
    domain_perms = await _get_permissions(user.domain)
    if domain_perms:
        if domain_perms.wildcard:
            return tool_name not in domain_perms.denied_tools
        return tool_name in domain_perms.allowed_tools
    
    # Default: deny
    return False
```

### ADK Integration

**Two-layer model — tool-class vs resource:**

| Layer | What it gates | Where | When checked |
|-------|---------------|-------|--------------|
| **Tool-class permission** | Can this user invoke this tool *at all*? (`ai_search`, `file_browser`, …) | `_before_tool` callback via `can_use_tool(user, tool.name)` | Every tool call (60s-cached Firestore read) |
| **Resource access** | Which bucket/folder/skill can the tool touch for *this* invocation? | `AccessContext` methods + pre-issued signed URLs in `tool_context.state` | Once at skill-start; reused for the whole run. See [resource-access-control.md](../resource-access-control.md) |

Tool-class permission is enforced in the `before_tool` callback:

```python
# In backend/adk/callbacks.py

async def _before_tool(tool, args, tool_context):
    """Check tool-class permission before execution.

    Resource-level access (bucket/folder/skill) is NOT checked here —
    it's already been resolved via AccessContext at skill-start, and
    the allowed resources / signed URLs are in tool_context.state.
    """
    user = await get_user_from_state(tool_context)

    if not await can_use_tool(user, tool.name):
        raise ToolPermissionDenied(
            f"User {user.email} does not have permission to use tool '{tool.name}'"
        )
```

### Frontend Auth Flow

```
[App Load]
    │
    ▼
[FirebaseProvider] — initialize Firebase SDK
    │
    ▼
[AuthProvider] — listen to auth state changes
    │
    ├── Not authenticated → show login page
    │       ├── Google Sign-In (primary)
    │       └── Email/Password (secondary)
    │
    └── Authenticated → store user + token in context
            │
            ▼
        [API calls include Authorization: Bearer <token>]
```

```typescript
// frontend/src/contexts/AuthContext.tsx
interface AuthContextType {
  user: User | null;
  loading: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  getToken: () => Promise<string>;  // For API calls
}
```

### API Route Protection

```python
# FastAPI dependency injection pattern

@app.get("/api/skills/{skill_id}")
async def get_skill_endpoint(
    skill_id: str,
    user: User = Depends(get_current_user),
):
    skill = await get_skill(skill_id)
    if not request.state.access.can_access_skill(skill):
        # 404, not 403 — do not leak existence of skills the user cannot see.
        # See resource-access-control.md "Security Considerations".
        raise HTTPException(404, "Not found")
    return skill


@app.put("/api/skills/{skill_id}")
async def update_skill_endpoint(
    skill_id: str,
    update: SkillUpdate,
    user: User = Depends(get_current_user),
):
    skill = await get_skill(skill_id)
    # If the user can't even see the skill, return 404 (same as GET).
    if not request.state.access.can_access_skill(skill):
        raise HTTPException(404, "Not found")
    # Can see, but cannot modify — this is a real 403.
    if not request.state.access.is_skill_owner(skill):
        raise HTTPException(403, "Only the skill owner can update")
    return await update_skill(skill_id, update)
```

### Architecture Diagram

```
[Frontend]
    │ Authorization: Bearer <firebase_jwt>
    ▼
[FastAPI Middleware]
    │ verify_id_token()
    ▼
[get_current_user] → User(uid, email, domain)
    │
    ├── [Skill endpoints] → has_skill_access(user, skill)
    │       └── accessControl check (public/private/domain/specific)
    │
    ├── [Skill CRUD] → is_skill_owner(user, skill)
    │       └── Owner-only write access
    │
    └── [Agent execution] → before_tool callback
            └── can_use_tool(user, tool_name)
                    └── Firestore: tool_permissions/{uid_or_domain}
```

### Firestore Security Rules

```javascript
// firestore.rules (safety net — primary enforcement is in API layer)
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    
    match /skills/{skillId} {
      // Anyone can read public skills
      allow read: if resource.data.accessControl.type == "public";
      // Authenticated users can read domain/specific skills (API enforces details)
      allow read: if request.auth != null;
      // Only owner can write
      allow write: if request.auth != null 
        && request.auth.uid == resource.data.ownerId;
      // Anyone authenticated can create
      allow create: if request.auth != null;
    }
    
    match /tool_permissions/{id} {
      // Admin-only (managed via scripts, not API)
      allow read: if request.auth != null;
      allow write: if false;
    }
  }
}
```

## Implementation Plan

### Phase 1: Firebase Auth Middleware (~1 day)
- [ ] Implement `backend/auth/firebase_auth.py` — `get_current_user()` dependency
- [ ] Initialize `firebase_admin` in `fast_api_app.py`
- [ ] Add auth to all existing endpoints (health excluded)
- [ ] Write tests with mock Firebase tokens

### Phase 2: Skill Access Control (~1 day)
- [ ] Implement `has_skill_access()` and `is_skill_owner()` in `backend/auth/permissions.py`
- [ ] Wire into skill CRUD endpoints
- [ ] Update Firestore security rules
- [ ] Write tests for all access control types

### Phase 3: Tool Permissions (~1 day)
- [ ] Port `tool_permissions.py` from v5 (strip Sunholo)
- [ ] Implement `can_use_tool()` with caching
- [ ] Wire into `_before_tool` callback
- [ ] Seed default permissions for dev environment
- [ ] Write tests for user-level, domain-level, and wildcard permissions

## Migration & Rollout

**Database Migrations:**
- `tool_permissions/` collection: seed from v5 data or create fresh for dev
- No schema changes to existing collections

**Rollback Plan:**
- Remove `Depends(get_current_user)` from endpoints → unauthenticated access (dev only)

## Testing Strategy

### Backend Tests (pytest)
- [ ] Valid JWT → user extracted correctly
- [ ] Invalid/expired JWT → 401
- [ ] Missing auth header → 401
- [ ] Private skill: owner can access, others cannot
- [ ] Public skill: anyone can access
- [ ] Domain skill: same-domain user can access, other-domain cannot
- [ ] Specific skill: listed email can access, unlisted cannot
- [ ] Tool permissions: allowed tool passes, denied tool raises
- [ ] Wildcard permissions work correctly
- [ ] Permission cache invalidation

### Frontend Tests
- [ ] Auth context provides user state
- [ ] Unauthenticated users redirected to login
- [ ] API calls include Bearer token

## Security Considerations

- Firebase JWT verification uses `firebase_admin` (server-side, not client SDK)
- Tokens are short-lived (1 hour) with automatic refresh on frontend
- Tool permissions cached for 60s — acceptable trade-off (permission changes take up to 60s to propagate)
- Firestore rules as safety net, not primary enforcement
- No PII logged in auth errors (log uid, not email)

## Performance Considerations

- JWT verification: ~5ms (Firebase Admin SDK caches public keys)
- Skill access check: <1ms (in-memory comparison)
- Tool permission check: <5ms (cached Firestore read)
- Permission cache TTL: 60s (same as v5)

## Success Criteria

- [ ] All authenticated endpoints reject invalid tokens
- [ ] Private skills accessible only by owner
- [ ] Domain skills accessible only by domain members
- [ ] Tool permissions enforced in before_tool callback
- [ ] No auth bypasses in security test suite
- [ ] Lint and typecheck clean

## Open Questions

- Should we support API keys for CLI/script access (in addition to Firebase JWT)?
- Should tool permissions be per-skill (skill A allows tool X) or global (user can use tool X everywhere)?
- Do we need an admin role for managing tool permissions via API (vs. Firestore console)?

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Security considerations (line 830-838)
- [Skills Data Model](skills-data-model.md) — AccessControl schema
- [Agent Factory](agent-factory.md) — before_tool callback integration
- [Resource Access Control](../resource-access-control.md) — extends this doc's access model to GCS buckets + folders; introduces `AccessContext` as the single request-scoped access cache (subsumes the ad-hoc `has_skill_access()` / `is_skill_owner()` functions sketched above)

---

## Implementation Report

**Completed**: 2026-04-21
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
