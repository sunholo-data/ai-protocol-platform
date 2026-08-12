# Template Auth Token Refresh

**Status**: ✅ Implemented in platform (2026-06-05) — **template sync pending**.
**Priority**: P1 (silent UX failure every Firebase-Auth-backed fork hits after ~1h)
**Estimated**: 1d planned; ~1h actual
**Scope**: Frontend (`lib/firebase.ts`, `providers/AGUIProvider.tsx`)
**Dependencies**: [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) G38 (no-token-refresh-unmount-gate contract — G40 mutates headers in place specifically to keep that contract)
**Created**: 2026-06-05
**Last Updated**: 2026-06-05
**Source items**: G40 / Friction 21 — downstream fork user (gde-ap-agent), 2026-06-05, after a long-running demo hit a 401 wall mid-session.

## Problem Statement

Every Firebase-Auth-backed v6 fork has the same trap: the AGUIProvider
calls `getIdToken()` once at mount and bakes the result into the
HttpAgent's `Authorization` header at construction time. Firebase ID
tokens expire after **~1 hour**. The next `runAgent()` call after expiry
gets a 401, the agent appears "broken" mid-session, and the only
workaround is a page refresh (which the fork user shouldn't have to
know to do).

**Current State (pre-G40):**

The AGUIProvider's token-fetch is a fire-and-forget one-shot:

```tsx
const [token, setToken] = useState<string | null>(null);
useEffect(() => {
  void getIdToken().then((t) => setToken(t));
}, [getIdToken]);

const agent = useMemo(() => new HttpAgent({
  url: …, headers: { Authorization: `Bearer ${token}` }, threadId: sessionId,
}), [skillId, token, sessionId]);
```

Even when `token` changed (e.g. on sign-in), the `useMemo` rebuilt the
agent — but `getIdToken()` was only called ONCE at mount, so the token
state never updated after the first fetch. After Firebase's silent
~hourly rotation, the in-memory `token` value is stale; the agent's
header carries an expired bearer; the backend returns 401.

**Impact:**

- **Universal across forks** — any fork using Firebase Auth + AGUIProvider hits this.
- **Invisible until it matters** — a 5-minute "process this invoice" demo never sees it; a multi-hour workshop, live judge demo, or anything resembling real work all surface it.
- **Silent UX failure** — the chat just stops responding. No error banner explains why. Console shows 401s. Users assume "the agent is broken."
- **Only workaround is page refresh** — which re-mounts the AGUIProvider and re-fetches the token. A fork user has no reason to know this.

**Why upstream Aitana hadn't hit it yet:** dev sessions are short; the workshop demo path was tested in <30min windows; QA never ran a session past the ~1h boundary. The gde-ap-agent fork found it during a long demo run.

## Goals

**Primary Goal:** Long-running Firebase-Auth sessions never hit a 401 mid-conversation. The HttpAgent's bearer header automatically tracks the latest valid token without rebuilding the agent or unmounting children.

**Success Metrics:**
- Agent's `headers.Authorization` reflects the latest Firebase-rotated token within one event loop of `onIdTokenChanged` firing.
- HttpAgent instance is **stable** across token rotations (no rebuild, no in-flight SSE stream loss).
- Pre-existing G38 contract holds: children never unmount on token refresh.
- LOCAL_MODE and anonymous-group-auth modes are no-ops (those tokens don't rotate the same way).

**Non-Goals:**
- Refreshing the token *on demand* before each request. Firebase's `onIdTokenChanged` already covers the silent ~hourly rotation; mid-rotation freshness is what `onIdTokenChanged` exists to provide. We don't add a per-request refresh loop.
- Anonymous-group token rotation. The `AnonymousGroupAuthProvider` owns that lifecycle (it writes new tokens to sessionStorage); subscribers don't get push-notified from `subscribeToIdToken` today. Forks needing tighter refresh wrap that provider.
- Server-side token validation changes. Backend's `Depends(get_current_user)` handles expiry verification already; this fix is purely client-side token-freshness.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Removes the "agent went dead after 1h" failure mode — long sessions stay responsive |
| 2 | EARNED TRUST | +1 | Users trust the system not to silently break mid-conversation; the silent 401 was a credibility hit |
| 3 | SKILLS, NOT FEATURES | 0 | Provider-level fix; no skill-shape change |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Auth concern, not model-routing |
| 5 | GRACEFUL DEGRADATION | +1 | Sign-out (token=null) strips the header instead of leaving a stale token; null-safe across modes |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses Firebase SDK's `onIdTokenChanged` primitive — protocol-native |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | +1 | Token rotations become observable via `subscribeToIdToken` — telemetry can wire to it later |
| 9 | SECURE BY CONSTRUCTION | +1 | Expired tokens are NEVER sent on the wire after rotation (the header mutates in place atomically with Firebase's notification) |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Same protocol, just better client-side token plumbing |
| | **Net Score** | **+5** | Acceptable — proceed |

**Conflict Justifications:** None (no -1 scores).

## Design

### Overview

Replace the AGUIProvider's one-shot `getIdToken()` + `setToken()` + `useMemo([token, …])` pattern with:

1. A new `subscribeToIdToken(callback)` helper in `lib/firebase.ts` that fires on every Firebase token rotation, AND degrades cleanly to a single-fire for LOCAL_MODE / anonymous-group / not-configured paths.
2. In AGUIProvider, build the HttpAgent **once** per `(skillId, sessionId)` with empty headers. Subscribe to `subscribeToIdToken` in a separate `useEffect`; on each callback, mutate `agent.headers.Authorization` **in place**. The agent instance is stable across rotations; no rebuild, no unmount.

### `lib/firebase.ts` — `subscribeToIdToken`

```ts
export function subscribeToIdToken(
  callback: (token: string | null) => void,
): () => void {
  if (isAnonymousGroupAuthMode()) {
    const session = readStoredGroupSession();
    callback(session?.token ?? null);
    return () => {};
  }
  if (isLocalMode()) {
    callback(LOCAL_MODE_STUB_TOKEN);
    return () => {};
  }
  const auth = getFirebaseAuth();
  if (!auth) {
    callback(null);
    return () => {};
  }
  return onIdTokenChanged(auth, () => {
    void getIdToken().then((t) => callback(t));
  });
}
```

Three guarded paths so the helper degrades cleanly across the platform's auth modes:
- **LOCAL_MODE** — synchronous one-shot with the stub (which never expires).
- **Anonymous-group** — synchronous one-shot with the stored group token. Group tokens are managed elsewhere (see Non-Goals).
- **Firebase Auth (configured)** — wires `onIdTokenChanged` so silent ~hourly rotations + sign-in/out events all surface via the callback. Token is re-resolved via `getIdToken()` so the user-null sign-out path delivers a clean `null` instead of throwing.
- **Firebase Auth (not configured)** — callback fires once with `null`, no listener wired.

### `providers/AGUIProvider.tsx` — in-place header mutation

```tsx
// Agent is stable across token rotations — token is NOT in the dep list.
const agent = useMemo(() => new HttpAgent({
  url: `/api/proxy/api/skill/${encodeURIComponent(skillId)}/stream`,
  headers: {},  // populated by the subscribeToIdToken effect below
  threadId: sessionId,
}), [skillId, sessionId]);

useEffect(() => {
  const unsubscribe = subscribeToIdToken((token) => {
    const headers = agent.headers as Record<string, string>;
    if (token) headers.Authorization = `Bearer ${token}`;
    else delete headers.Authorization;
  });
  return unsubscribe;
}, [agent]);
```

**Why in-place mutation:** HttpAgent reads `agent.headers` on every outbound request. Mutating the existing headers object means a token rotation propagates to the next request without:
- Rebuilding the HttpAgent (which would lose any in-flight SSE stream state).
- Re-rendering React (no React state change involved).
- Triggering React-tree effects (e.g. `useSessionMessages` doesn't refire its GET).
- Violating the G38 no-unmount contract (children never see the rotation as a re-render).

**Why drop `token` from the `useMemo` dep list:** pre-G40 each token landing forced an agent rebuild. With in-place mutation that rebuild becomes redundant — and worse, it discards SSE stream state mid-stream. G40 lets the agent live the full session.

### Pre-existing contract preserved

The G38 "AGUIProvider must not unmount children on token refresh" contract from [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) holds — verified by a pre-existing test that re-renders the provider multiple times with a stable ref check on the child DOM node. G40's in-place mutation pattern is the natural way to honour both G38 (don't unmount) AND token-freshness without contradiction.

### CLI Surface

No new commands.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add `subscribeToIdToken` helper to `frontend/src/lib/firebase.ts` with the four guarded paths | 30min |
| 2 | Rewrite AGUIProvider: drop `token` state, drop `token` from `useMemo` deps, add `useEffect` that subscribes + mutates headers in place | 30min |
| 3 | Update existing AGUIProvider tests to assert empty headers at construction + auth header via post-mount subscription | 30min |
| 4 | Add new G40 contract test: token rotation mutates header in place without rebuild OR unmount | 15min |
| 5 | Add subscribeToIdToken unit tests covering LOCAL_MODE / anonymous / Firebase / sign-out / unsubscribe-passthrough paths | 45min |
| 6 | New design doc + SEQUENCE row | 15min |

**Total: ~2.75h ≈ ~3h** (rounded up). Compressed in execution to ~1h because the helper turned out small and the test patterns reused existing mocks.

## Testing Strategy

**`frontend/src/lib/__tests__/subscribeToIdToken.test.ts`** (7 cases):
- LOCAL_MODE: fires once with stub token; no Firebase wire-up; unsubscribe is no-op.
- Anonymous-group with stored token: fires once with token; no Firebase wire-up.
- Anonymous-group without stored session: fires once with null.
- Firebase Auth not configured: fires once with null; no `onIdTokenChanged` registered.
- Firebase Auth configured: registers `onIdTokenChanged`; each handler fire re-resolves the token and delivers it (covers initial sign-in + ~hourly rotation).
- Firebase Auth sign-out: handler fires with `currentUser=null`; getIdToken returns null; callback gets null.
- Firebase Auth unsubscribe is the one Firebase gave us (passthrough).

**`frontend/src/providers/__tests__/AGUIProvider.test.tsx`** (G40 cases):
- "Renders children, builds an HttpAgent at the skill stream endpoint, and mutates the auth header in place" — assert ctor was called with `headers: {}` and the agent's live `headers.Authorization` becomes `Bearer test-token` after subscription fires.
- "G40: token rotation mutates agent.headers.Authorization in place without rebuilding the agent" — fire a rotation via mock-controlled `tokenSubscribers`; assert (a) header swapped, (b) same agent instance via `useAGUIAgent()`, (c) `httpAgentCtor.mock.calls.length` stays at 1.
- "G40: sign-out (token=null) strips the Authorization header" — fire rotation with null; assert `Authorization` is undefined, agent still the same instance.

Pre-existing tests preserved: URL-encoding of skillId, `useAGUIAgent` throws outside provider, D1 sessionId-change rebuild, G38 children-stay-mounted contract.

## Success Criteria

- [x] `subscribeToIdToken` helper ships in `lib/firebase.ts` with 7 unit tests covering all four guarded paths.
- [x] AGUIProvider builds the HttpAgent with empty headers; subscribes to token via `subscribeToIdToken`; mutates `agent.headers.Authorization` in place.
- [x] After Firebase rotates the ID token, the HttpAgent instance is the same (no rebuild) AND its `headers.Authorization` reflects the new token.
- [x] Sign-out (token=null) strips the header without rebuilding the agent.
- [x] G38 contract preserved: children stay mounted across re-renders.
- [x] All existing AGUIProvider tests still pass with the updated mock for `subscribeToIdToken`.
- [x] `npm run quality:check` passes (lint + tsc + tests + build).
- [ ] **Template sync pending**: next `aitana-template-publish` run propagates to `sunholo-data/ai-protocol-platform`.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — G40 row
- [template-chat-surface-defaults.md](./template-chat-surface-defaults.md) — G38 (no-unmount-on-token-refresh contract); G40 mutates headers in place specifically to preserve G38
- [template-auth-hardening.md](./template-auth-hardening.md) — sibling auth-cluster doc covering different items (#9, #19, #20, #21)
