# External-host MCP authentication (OAuth 2.1)

**Status**: Proposed
**Priority**: P2 (Low) — unblocks production external-host access (ChatGPT/Copilot connectors); not pilot-blocking. Gated on a real "expose sims to external users" decision.
**Estimated**: ~1d resource-server layer + IDP-spike (Phase 1) · ~3–4d platform-as-AS (Phase 2, if chosen)
**Scope**: Backend (auth ingress + MCP resource-server metadata) + CLI probe. No frontend.
**Dependencies**: [external-host-mcp-apps.md](../aipla/v1.1.0-feedback/external-host-mcp-apps.md)-style external serving; `backend/protocols/mcp_proxy.py` (the existing Firebase-gated path); `backend/auth/` (Firebase + group-id auth, the token-shape dispatcher in `auth/__init__.py`); the dev-only `infrastructure/mcp-artefact-test/` harness.
**Created**: 2026-07-08
**Last Updated**: 2026-07-08

> **Origin:** MCP-Apps workshop (2026-07-08). We proved sims render + interact in
> ChatGPT dev mode over an **anonymous** cloudflared tunnel. The last-mile for
> production is authentication — ChatGPT expects an OAuth 2.1 flow. This doc
> scopes it. Research verified 2026-07-08 against the sources cited below.

## Problem Statement

The platform's deployed `/mcp` is reachable **only** through
`backend/protocols/mcp_proxy.py`, which enforces a **Firebase JWT + per-skill
allowlist** (the frontend sends the user's Firebase Bearer; the proxy validates,
strips it, forwards internally). An **external** MCP host — ChatGPT dev-mode
connector, Copilot, Claude — has **no Firebase token** and cannot traverse that
proxy. So today the only way to expose an artefact/sim to an external host is
**anonymous** (the cloudflared dev path + `infrastructure/mcp-artefact-test/`).

**Current State:**
- External-host access = anonymous, dev-only. Fine for a workshop; **not** for
  production (anyone with the URL loads the sims into their own ChatGPT).
- The MCP Authorization spec (OAuth 2.1) is the standard for authenticated remote
  MCP; ChatGPT connectors implement the client side of it. We implement none of
  the server side.

**Impact:** blocks any production/"bring-your-own-AI" external surface (teacher
uses a platform sim inside their own ChatGPT under their platform identity), and
blocks the ChatGPT app-store path (OAuth is a submission prerequisite).

## Goals

**Primary Goal:** An external MCP host (ChatGPT first) can authenticate to the
platform's MCP endpoint via **OAuth 2.1 (auth-code + PKCE)** per the MCP
Authorization spec, and each request is bound to a real platform `User` — with
**no third-party auth server hand-rolled** (Option B rejected).

**Success Metrics:**
- A ChatGPT dev-mode connector to the (authenticated) endpoint completes login →
  consent → authenticated `tools/call`; the validated token `sub` maps to a
  platform `User` and the existing per-skill allowlist applies.
- Unauthenticated requests get `401` + `WWW-Authenticate` and trigger the host's
  login UI (not a silent failure).
- Zero regression to the existing Firebase-proxied `/mcp` path.

**Non-Goals:**
- Building an OAuth authorization server from scratch (Option B — rejected).
- Auth for the **dev** harness — `mcp-artefact-test` stays anonymous/dev-only.
- Claude Desktop / Copilot enrolment specifics (host fragmentation, §Design) —
  the resource-server layer is shared; per-host enrolment is follow-up.

## Axiom Alignment

Score per [Product Axioms](../../product-axioms.md). Net ≥ +4, ≤2 conflicts.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Adds a one-time connect/consent round-trip, not a per-request cost. |
| 2 | EARNED TRUST | 0 | Not a factual-claims feature. |
| 3 | SKILLS, NOT FEATURES | 0 | Infra; invisible to end users (they just log in). MCP is the sanctioned extensibility surface (Axiom 3 tradeoff). |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Explicit failure modes: missing/expired/invalid token → `401` + `WWW-Authenticate` → host login; IDP/JWKS unreachable → deny with a clear error, never open. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Adopts the **MCP Authorization spec** wholesale (OAuth 2.1, RFC 9728/8414/7636/8707/7591). Explicitly rejects a custom auth scheme (Option B). |
| 7 | API FIRST | +1 | External hosts become a channel over one standard-auth API surface — any MCP-standard client works, not just ChatGPT. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Extends the sprint-2.14 tenant attribution: the validated OAuth `sub`/`aud` become span attributes; token-validation failures are traced. |
| 9 | SECURE BY CONSTRUCTION | +1 | The feature **enforces** auth architecturally (deny-by-default, audience-bound tokens, signature verification). **Caveat:** Option A adds an external IDP trust relationship (login crosses the GCP edge) — see Conflict-adjacent note; **Option A′ keeps identity inside the edge** and is preferred for exactly this reason. Not scored −1 (it adds enforcement, not a leak). |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend/ingress only; the host runs the OAuth dance, not our frontend. |
| | **Net Score** | **+5** | Threshold ≥ +4 ✅ |

**Conflict Justifications:** none scored −1. **Axiom-9 watch item:** Option A
routes user login through a third-party IDP (data crosses the project edge) and
therefore needs an explicit egress justification per Axiom 9; **Option A′ avoids
it** by keeping login on Firebase inside the GCP project — a reason the doc
leans A′ for production.

## Standards Compliance (Axiom #6)

No custom format. We implement the **existing** standards the MCP Authorization
spec composes:

| Concern | Standard |
|---|---|
| Protected-resource metadata | **RFC 9728** — `GET /.well-known/oauth-protected-resource` |
| Authorization-server metadata | **RFC 8414** (`/.well-known/oauth-authorization-server`) or OIDC Discovery |
| Auth-code + **PKCE** (S256) | **RFC 7636** (mandatory) |
| Audience binding | **RFC 8707** resource indicators — token `aud` == our resource id |
| Dynamic Client Registration (optional) | **RFC 7591** — or ChatGPT's **CIMD** (preferred, skips DCR) |

The TS MCP SDK and Python FastMCP both ship bearer-auth + metadata helpers, so
the resource-server layer is ~config, not crypto.

## Design

### The decision: who runs the authorization server (A vs A′ — NOT B)

**A/A′/B is "who issues tokens," not "demo vs prod."** All three can be
production; the effort and trust-surface differ enormously.

| | **A — third-party IDP** | **A′ — platform is its own AS (reuses Firebase)** | **B — hand-rolled AS** |
|---|---|---|---|
| We implement | resource-server only (metadata + token validation) | resource-server **+** `/authorize` `/token` `/register` `/jwks` via an OAuth **library** (e.g. `authlib`), login backed by existing Firebase | everything in A′ **by hand**, incl. crypto |
| Login/consent/PKCE/JWKS/refresh | the **IDP** (Auth0/Stytch/WorkOS/Okta) | the **library** + our Firebase login page | **us** |
| Identity of record | IDP (or IDP federates to Firebase) | **existing platform/Firebase accounts** | ours |
| Privacy boundary (Axiom 9) | login crosses the GCP edge → needs egress justification | **stays inside the GCP edge** | inside, but self-built |
| Effort | ~½–1d + IDP console | ~3–4d (library-backed) | weeks + permanent security liability |
| Verdict | fast path / PoC | **production target** | **rejected** |

**Recommendation — phased:**
- **Phase 1 (fast, reversible): Option A** with a free **Auth0 or Stytch** tenant
  to prove the ChatGPT connector flow end-to-end and to ship the shared
  resource-server layer. Low risk; dev only.
- **Phase 2 (production): Option A′** — the platform becomes its own OAuth AS via
  `authlib`, backed by Firebase login, so external users use **existing platform
  accounts**, no vendor, and identity never leaves the GCP edge (Axiom 9).
  Decide to proceed only when a real external-user surface is committed.

Both phases share the same **resource-server** work below — that's the reusable core.

### Resource-server layer (shared by A and A′)

The platform's MCP endpoint becomes an OAuth 2.1 **resource server** for external
callers:

1. **Protected-resource metadata** — new route
   `GET /.well-known/oauth-protected-resource` (RFC 9728):
   ```json
   {
     "resource": "https://<external-mcp-host>/mcp",
     "authorization_servers": ["https://<issuer>"],
     "scopes_supported": ["mcp:read", "mcp:tools"],
     "bearer_methods_supported": ["header"]
   }
   ```
2. **Bearer validation middleware** on the external ingress — verify JWT signature
   against the issuer **JWKS** (cached), check `iss`, `aud == resource id`
   (RFC 8707), `exp`/`nbf`, required scopes. Missing/invalid →
   `401 WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource", error="invalid_token"`,
   and on a tool call, `_meta["mcp/www_authenticate"]` to trigger the host login UI.
3. **`sub` → `User` mapping** — add an `auth_mode: "oauth"` branch to the
   token-shape dispatcher in `auth/__init__.py` (mirrors how **group-id-auth**
   was added in sprint 2.11). Resolve `sub` to a platform `User`; reuse the
   existing **permissions + per-skill allowlist**.
4. **Tool security schemes** — sim/MCP tools declare
   `securitySchemes: [{ "type": "oauth2", "scopes": ["mcp:tools"] }]`.

### Where it sits vs `mcp_proxy` (two surfaces, coexisting)

These are **different ingress surfaces on the same MCP server** and must coexist:

- **Existing (unchanged):** frontend → `/api/proxy/mcp` → `mcp_proxy` (Firebase
  Bearer, allowlist, strips auth, forwards internally). This is the in-app path.
- **New:** a **dedicated external ingress** (e.g. `/external/mcp`, or `/mcp` with
  the resource-server middleware) that accepts an **OAuth Bearer directly** from
  the host, validates it (above), maps `sub`→User, and serves the **same** MCP
  tool surface. Separate route keeps rate limits, the `.well-known` scoping, and
  the anonymous-vs-authenticated posture clean.

The auth dispatcher chooses Firebase / group-id / **oauth** by token shape — no
change to the in-app path.

### ChatGPT specifics (Apps SDK)

- Remote-only (HTTPS); register redirect `https://chatgpt.com/connector/oauth/{callback_id}`
  in the AS. Prefers **CIMD** (`client_id_metadata_document_supported: true` → no
  DCR); DCR and pre-registered clients also supported.
- Auth-code + PKCE (S256); the `resource` param must be echoed into token `aud`.

### Host fragmentation (note, not scope)

Claude uses **connectors/extensions** (and `mcp-remote`) rather than the same
OAuth-connector UX; Copilot enrols via the **M365 Agents Toolkit**. The
resource-server layer is host-agnostic (it's the MCP spec); per-host enrolment
docs are a follow-up.

### Dev vs prod

`infrastructure/mcp-artefact-test/` stays **anonymous/dev-only** (workshop +
local iteration). Auth is a production concern for the deployed endpoint only.

## API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/.well-known/oauth-protected-resource` | RFC 9728 resource metadata | No (new) |
| GET | `/.well-known/oauth-authorization-server` | RFC 8414 AS metadata — **Phase 2 / A′ only** (Phase 1 the IDP serves this) | No (new) |
| POST/GET/DELETE | `/external/mcp` | OAuth-gated external MCP ingress (Streamable HTTP) | No (new) |
| (unchanged) | `/api/proxy/mcp` | existing Firebase-gated in-app path | No |

## CLI Surface

Add to the `aitana` CLI ([local-dev-cli.md](../v6.1.0/local-dev-cli.md)):
- **`aitana mcp probe <url>`** — smoke-test the OAuth resource-server surface:
  GETs `/.well-known/oauth-protected-resource`, does a token-less `initialize`
  expecting `401` + a well-formed `WWW-Authenticate`, and prints the discovered
  `authorization_servers`. The typed replacement for curl-by-hand when wiring a
  host. (~0.2d.)

## Implementation Plan

### Phase 1 — resource-server layer + Option-A spike (~1d)
- [ ] `/.well-known/oauth-protected-resource` route (~30 LOC).
- [ ] Bearer JWT validation middleware (JWKS cache + iss/aud/exp/scope) →
      `401`/`WWW-Authenticate`; `auth_mode:"oauth"` in the dispatcher (~80 LOC + tests).
- [ ] `securitySchemes` on the sim tools.
- [ ] Free **Auth0/Stytch** tenant; register ChatGPT redirect; prove the
      connector flow end-to-end (dev). `aitana mcp probe`.

### Phase 2 — Option A′ (platform-as-AS), if committed (~3–4d)
- [ ] `authlib`-backed `/authorize` `/token` `/jwks` + `/.well-known/oauth-authorization-server`,
      login via existing Firebase; CIMD/DCR support; consent screen.
- [ ] `resource`→`aud` binding; refresh handling; JWKS rotation.
- [ ] Migrate the resource-server `authorization_servers` from the IDP to self.

## Migration & Rollout

**Feature flag:** external ingress off by default; enable per-env (dev first).
**Rollback:** the external route is additive — disable the flag; the in-app
Firebase path is untouched. **Exposure gate:** as in the AIPLA external-host doc,
serving sims to external users is a product/privacy decision — sign-off before
advertising the endpoint. **Env:** IDP issuer/JWKS URL + client config (Phase 1);
`authlib` signing keys in Secret Manager (Phase 2).

## Testing Strategy

- **Backend (pytest):** token-less request → `401` + valid `WWW-Authenticate`;
  valid token → `sub`→User + allowlist enforced; wrong `aud` / expired / bad-sig
  → `401`; JWKS-unreachable → deny (not open); regression: the Firebase proxy
  path unchanged.
- **Manual (dev):** ChatGPT dev-mode connector → login → consent → authenticated
  `show-artefact` + `increment-counter`; confirm `sub` in traces.
- **CLI:** `aitana mcp probe` asserts the discovery + 401 contract.

## Security Considerations

- **Deny-by-default:** no valid token → no access; audience-bound tokens
  (RFC 8707) prevent token passthrough/confused-deputy.
- **Privacy boundary (Axiom 9):** Option A routes login through an external IDP →
  document the egress + prefer **A′** (identity stays on Firebase inside the GCP
  edge) for production. Never put prompts/PII on the IDP beyond the auth minimum.
- **No token leakage:** validate at the ingress; never forward the external
  Bearer to internal services (mirror `mcp_proxy`'s strip-and-map posture).
- **Scope minimalism:** `mcp:read` / `mcp:tools`; sims are read-ish; the
  mutation tools require the tools scope.

## Success Criteria

- [ ] `GET /.well-known/oauth-protected-resource` returns valid RFC 9728 metadata.
- [ ] Token-less request → `401` + `WWW-Authenticate` (host shows login).
- [ ] ChatGPT dev-mode connector completes OAuth; authenticated `tools/call`
      succeeds; `sub`→User + allowlist applied.
- [ ] Wrong-aud / expired / bad-sig tokens rejected; JWKS-down fails closed.
- [ ] Existing Firebase-proxied `/mcp` path unregressed (pytest green).
- [ ] `aitana mcp probe <url>` passes against the authenticated endpoint.

## Open Questions

- **A vs A′ for production** — accept the IDP vendor/egress (A, fast) or build the
  authlib-backed AS on Firebase (A′, privacy-clean)? Recommend A for the PoC, A′
  for prod; decide when a real external surface is committed.
- **CIMD vs DCR** for ChatGPT — CIMD is preferred (no per-connector registration);
  confirm the chosen IDP/authlib advertises `client_id_metadata_document_supported`.
- **Per-tenant scoping** — should an external OAuth user see only their client's
  skills? Reuse the group-tag/allowlist policy; confirm mapping from `sub`.

## Related Documents

- [external-host-mcp-apps.md](../aipla/v1.1.0-feedback/external-host-mcp-apps.md) — serving sims to external hosts (the anonymous/dev predecessor).
- [mcp-apps-iframe-guide.md](../../ops/mcp-apps-iframe-guide.md) — the cross-host bridge + `mcp-artefact-test` harness.
- [protocol-gotchas.md](../../workshop/protocol-gotchas.md) #12 — ChatGPT bridge.
- Sprint 2.11 anonymous-group-id-auth — the pattern for adding a new `auth_mode` to the dispatcher.
- **External:** [MCP Authorization spec (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) · [OpenAI Apps SDK — Authentication](https://developers.openai.com/apps-sdk/build/auth) · [Auth0: MCP server in ChatGPT](https://auth0.com/blog/add-remote-mcp-server-chatgpt/) · [Stytch: auth for the Apps SDK](https://stytch.com/blog/guide-to-authentication-for-the-openai-apps-sdk/) · RFCs 9728 / 8414 / 7636 / 8707 / 7591.
