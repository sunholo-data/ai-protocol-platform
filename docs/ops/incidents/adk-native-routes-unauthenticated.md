# SEC-1 — ADK's own routes served to the public internet unauthenticated

- **Status:** Fixed on dev in [`b6b39a7`](https://github.com/sunholo-data/ai-protocol-platform/commit/b6b39a7) (2026-08-07); shipped to test as `v6.23.1`.
- **Severity:** High — unauthenticated read of customer conversation content, plus unauthenticated destructive and billable operations.
- **Scope:** dev, test (including the public `test.yourcompany.com` domain). Prod v6 was not serving and would have inherited it at the 1 Sept cutover.
- **Known exploitation:** none observed. No access-log review has been done — see *Open follow-ups*.
- **Found:** while mapping the three trace stores for [trace-completeness-and-access](../../design/v6.23.0/trace-completeness-and-access.md) Phase 1. Not found by a security review.

## TL;DR

`get_fast_api_app(web=True, …)` mounts ADK's own routes. None of them carry our
`Depends(get_current_user)`. Our proxy is a deliberate catch-all that forwards
any path and relies **entirely** on the backend to authenticate. The two facts
together meant ADK's routes were public.

```
GET /api/proxy/api/skills                                -> 401   correct
GET /api/proxy/apps/aitana_platform/users/{uid}/sessions -> 200   (!!)
```

Neither component was wrong on its own. The proxy is *supposed* to forward
everything; the backend is *supposed* to be the auth boundary. The gap was that
a whole family of routes had been added to the backend by a library, and nothing
asserted that every route on the app has an auth story.

## What was exposed

| Route | Exposure |
|---|---|
| `GET /apps/{app}/users/{uid}/sessions` | List any user's conversations |
| `GET /apps/{app}/users/{uid}/sessions/{sid}` | **Full event list** — every message, tool call and extracted clause. ONE's PPA contract content: precisely what CLAUDE.md's security hard rule governs. |
| `GET …/sessions/{sid}/artifacts…` | Parsed documents |
| `DELETE …/sessions/{sid}` | Destroy a conversation |
| `PATCH /apps/{app}/users/{uid}/memory` | Write to a user's memory |
| `POST /run`, `/run_sse` | Execute the agent — arbitrary invocation, billed to us |
| `/debug/trace/…`, `/dev-ui`, `/builder/…` | Debug surface + ADK dev UI |

A Firebase uid is not a secret — it appears in URLs, logs and admin surfaces.
Obscurity was never the boundary.

## Verification method (deliberately minimal)

Reachability was proven with a **non-existent uid** (`zzz-nonexistent-probe`),
which returned `[]`. That establishes the auth boundary is absent without
reading any real user's data. No destructive verb was exercised; the list of
affected routes above comes from reading `/openapi.json`, not from calling them.

## The fix

Middleware in [`backend/fast_api_app.py`](../../../backend/fast_api_app.py) that
denies ADK-native path prefixes unless the caller resolves to an admin scope.

Middleware rather than per-route dependencies, for two reasons:

1. The routes are registered by ADK — we cannot decorate them.
2. **A future `google-adk` bump that adds a route would silently reopen the
   hole.** Prefix-denial covers new routes the day they appear.

It reuses the app's real auth entry point (`get_current_user` → the same
Firebase / group-auth / LOCAL_MODE dispatch every other route uses) and
`resolve_admin_scope`, so admin tokens keep working — the `aitana-adk-testing`
skill is unaffected once it mints a token — and LOCAL_MODE dev is unchanged.
It fails **closed** if the auth layer raises unexpectedly.

`/openapi.json`, `/docs`, `/redoc` are left public deliberately: they expose API
*shape*, not content, and `curl /openapi.json | jq '.paths'` is the documented
endpoint-discovery step in CLAUDE.md. That decision is recorded explicitly in
the test rather than left as an accident of prefix matching.

## Guard against regression

[`backend/tests/api_tests/test_adk_native_route_guard.py`](../../../backend/tests/api_tests/test_adk_native_route_guard.py)
— 32 tests. The load-bearing one walks the **live route table** rather than a
fixture list, so any route that is neither ours nor guarded fails CI. It already
earned its keep by catching the FastAPI doc routes during authoring.

Verified against a running backend, not only pytest: the ADK paths went 200 →
401 through both the backend directly and the public proxy; `/health` and
`/api/local-mode-status` stayed 200; the chat page still rendered and streamed.

## Why it survived this long

- The routes came from a library call (`web=True`), so they never appeared in a
  diff as "a new endpoint" and never got an auth review.
- The proxy's catch-all design is correct and documented, and its own regression
  guard asserts that `/api/proxy/api/skills` **without** a Bearer returns 401 —
  which it does. The guard tested one path, not the property.
- `/list-apps` is even overridden in our code (to fix a dev-UI quirk), so the
  file was edited without anyone asking what else `web=True` had mounted.
- **It was spotted and written down, then never decided.** The
  `aitana-adk-testing` skill said, verbatim: *"Auth is off for these — they're a
  dev/admin surface. When you bring `platform-backend` up in test/prod, decide
  whether to leave them open or strip them via a sub-app mount; not decided yet
  — flag it during deploy review."* The env was cut, the flag was never raised,
  and a note in a skill file is not a blocker. An open security question needs
  to live somewhere that fails a build, not somewhere that fails a reader.

The generalisable lesson: *an auth boundary asserted on a sample of routes is
not asserted.* The new test checks the property over the whole route table.

## Open follow-ups

- [ ] **Access-log review** on dev/test for hits to `/apps/…`, `/run`, `/debug/…`
      from outside our IPs, to convert "no known exploitation" into a checked
      statement. Not yet done.
- [ ] **Decide whether prod should run `web=False`.** Admin-auth is now enforced
      everywhere; disabling the dev UI in prod entirely would shrink the surface
      further at the cost of live prod session inspection.
- [ ] **Consider a proxy deny-list** for `/apps/`, `/debug/`, `/builder/`,
      `/dev-ui`, `/run*` as defence in depth. The backend is the correct
      boundary and is now closed, so this is belt-and-braces, not a fix.
- [ ] **Audit other mounted sub-apps** the same way — the MCP mount (`/mcp/…`)
      and the A2A card were not part of this review.

## Related

- [trace-completeness-and-access](../../design/v6.23.0/trace-completeness-and-access.md) — the work that surfaced this
- [`.claude/skills/aitana-adk-testing/SKILL.md`](../../../.claude/skills/aitana-adk-testing/SKILL.md) — the only consumer of these routes; now needs an admin token
- [fe-bringup-1-proxy-404](fe-bringup-1-proxy-404.md) — the proxy's original design and why it forwards everything
