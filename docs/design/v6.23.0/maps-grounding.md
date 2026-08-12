# maps-grounding — geospatial grounding via Maps Grounding Lite

**Status**: Implemented — dev + test live (v6.28.0), open to ONE; prod held
**Priority**: P2 (Low) — capability addition, not a UAT punch-list item
**Estimated**: ~1 day (0.5d shipped + 0.25d infra + 0.25d skill/attribution)
**Scope**: Backend (MCP registry + seed), infra (new `your-maps-project` project + per-env keys), no frontend work
**Dependencies**: [v6.1.0 mcp-app-integrations](../v6.1.0/implemented/mcp-app-integrations.md) (registry), [v6.14.0 mcp-toolbox-database-gateway](../v6.14.0/mcp-toolbox-database-gateway.md) (the "remote server = one Firestore row" pattern)
**Created**: 2026-08-11
**Last Updated**: 2026-08-12

## Problem Statement

The platform has no geospatial capability. A skill asked "what's near this
address", "how long is the drive between these two sites", or "what's the
weather at the delivery point" has to answer from model priors — which for
places is exactly where models hallucinate hardest, because a plausible
restaurant name is indistinguishable from a real one at generation time.

ADK 1.31.1 — the version already pinned in
[`pyproject.toml`](../../../backend/pyproject.toml) — ships
`google_maps_grounding`, a built-in model tool that would appear to close this
in one line. **It is not available to us.** That is the whole design decision,
so it is stated first.

## Constraint C1 — the native tool is contractually unavailable (EEA)

Grounding with Google Maps carries an explicit territorial exclusion:

> "Service not available for customers with billing addresses in the European
> Economic Area (EEA)."
> — [Maps Platform](https://mapsplatform.google.com/resources/blog/grounding-with-google-maps-in-vertex-ai-is-now-in-preview/)

Aitana Labs bills from the EEA. This is not a quota, a region setting, or a
preview gate that lapses at GA — it is a terms restriction on who may use the
service, and no `GOOGLE_CLOUD_LOCATION` value changes it.

Google's documented path for EEA customers is
[**Maps Grounding Lite**](https://developers.google.com/maps/ai/grounding-lite):

> customers may use the Maps Grounding Lite API to ground an LLM if they comply
> with the Generative AI Prohibited Use Policy and include associated Google
> Maps source links with the Grounded Output

**Do not "simplify" this back to `google_maps_grounding`.** The import resolves,
the ADK tool constructs, and the call may even succeed — none of which makes it
licensed. A future reader who sees a three-line native option beside forty lines
of MCP wiring will be tempted; C1 is why the trade is not what it looks like.

## Constraint C2 — a built-in tool cannot share a request with FunctionTools

Independent of C1, and worth recording because it shapes what "adding a tool"
costs here. `google_maps_grounding` is a **model-level** tool: ADK's
implementation appends `types.Tool(google_maps=types.GoogleMaps())` to the
request config rather than exposing a callable. Like `google_search`, it cannot
coexist with FunctionTools on the same agent request — the constraint documented
at the top of [`tools/search_agent.py`](../../../backend/tools/search_agent.py),
which is why web search is wrapped in an `AgentTool` sub-agent at all.

So even without the EEA exclusion, the native path would have cost a
`create_maps_agent()` sub-agent plus a `wants_maps` branch in
`_resolve_search_tools`, and would have worked **only on Gemini skill agents**.

## Why Grounding Lite is structurally the better fit anyway

Grounding Lite is an ordinary remote MCP server. Consequences, all of which fall
out for free:

| | Native `google_maps_grounding` | Grounding Lite (MCP) |
|---|---|---|
| EEA-licensed | ❌ | ✅ |
| Coexists with FunctionTools | ❌ needs sub-agent wrapper | ✅ ordinary toolset |
| Works on Claude / OpenAI skills | ❌ Gemini-only | ✅ provider-agnostic |
| New backend code to add the server | sub-agent + resolver branch | **none** — one Firestore row |
| Location biasing | not exposed by ADK's built-in | tool args |

The last row is the one that decided the estimate: the registry in
[`tools/mcp/registry.py`](../../../backend/tools/mcp/registry.py) already turns a
`{url, transport, headers}` document into a live toolset. Grounding Lite needs
no new resolution code at all.

Endpoint `https://mapstools.googleapis.com/mcp`, streamable HTTP, 300 QPM per
tool per project. The docs advertise three tools; a live `tools/list` against
the deployed key (2026-08-12) returns **five** — `search_places`,
`lookup_weather`, `compute_routes`, plus `resolve_names` and `resolve_maps_urls`,
the two endpoints the docs describe as experimental REST-only. Treat the extra
two as unstable: they are undocumented as MCP tools and may change without
notice.

## The one piece of real engineering: secret-bearing headers

Every MCP server seeded before this one has `headers: {}` — they are loopback
sidecars or unauthed, so no credential ever had to reach the registry.
Grounding Lite authenticates with `X-Goog-Api-Key`, making it the first server
that needs one.

A raw API key must not sit in a Firestore document. `mcp_servers/*` is readable
by anything holding the runtime SA, a hardcoded value would land in the seed
script and therefore in git, and it would persist in Firestore backups.

**Design**: a header *value* may be written as `"${MAPS_GROUNDING_API_KEY}"`.
`_build_toolset` resolves it from the process environment (mounted from Secret
Manager) when the toolset is built. Firestore stores the **name** of the secret;
only the process holds its value.

Resolution is exact-match on the whole value, not substring substitution — a
credential is the entire header value in every real case, and partial
interpolation invites a half-built `Authorization` header that fails in a much
more confusing way.

**An unresolved reference is a hard failure.** `_build_toolset` returns `None`,
the server is reported missing, and `resolve_mcp_tools_strict` raises. It must
never fall through to sending the literal `${VAR}` as a credential: that yields a
401 at `tools/list`, which ADK surfaces as "MCP server returned no tools" — the
indistinguishable silent misconfiguration that G42
([template-mcp-strict-resolution](../template/template-mcp-strict-resolution.md))
exists to make loud. This is CLAUDE.md #8 applied to a config path.

## Compliance obligation — attribution is not optional

Grounding Lite's terms require that Google Maps sources **immediately follow the
generated content they support** and be **viewable within one user interaction**.
For `search_places`, links must use the `places.googleMapsLinks.placeUrl` field
from the response.

This is a licence condition, not a UI preference. Note it differs in kind from
the existing Sources tab: that is built from Gemini **grounding metadata**
([`a2ui_sources_render.py`](../../../backend/adk/a2ui_sources_render.py)), and
Grounding Lite returns `placeUrl` in an ordinary **tool result** instead — so it
does not flow into the existing renderer for free. **Discharged by M4** — the
attribution mapping shipped alongside the first skill that declares the server,
never after it.

One nuance worth recording rather than glossing: the links land in a workbench
tab, which is *adjacent to* rather than literally beneath the prose. That reads
as "viewable within one user interaction" (a tab click), and it is the same
treatment web-search citations already get. If a stricter reading is ever
required, the fix is to render the card inline in chat rather than to change
where the data comes from.

## Milestones

### M1 — secret-bearing headers in the registry ✅ Done 2026-08-11

- `_resolve_header_secrets` in `tools/mcp/registry.py`; `${ENV_VAR}` values
  resolved from the environment, plain values untouched.
- Unresolved or empty → `UnresolvedHeaderSecret` → `_build_toolset` returns
  `None` → strict resolution raises.
- 7 tests in `tests/tool_tests/test_mcp_registry.py`, covering the pass-through,
  unset, blank-secret, and end-to-end strict-raise paths, plus a guard asserting
  the seeded config contains a secret *name* and never a key.

### M2 — registry seed ✅ Done 2026-08-11

- `MAPS_GROUNDING_CONFIG` + `seed_maps_grounding()` in
  `backend/scripts/seed_mcp_servers.py`.
- No `--url` override: the endpoint is Google's and identical in every
  environment. What differs per env is the secret behind the header reference,
  which Cloud Run mounts — so the row promotes cleanly.
- `--set-secrets=MAPS_GROUNDING_API_KEY=…` added to `backend/cloudbuild.yaml`;
  documented in `backend/.env.example`.

### M3 — infra ✅ dev + test live 2026-08-12 (prod deliberately held)

`your-maps-project` (project number `YOUR-PROJECT-NUMBER`) created by Mark inside folder
`YOUR-FOLDER-ID`, billing `YOUR-BILLING-ACCOUNT`. Terraform applied on dev and
test; each env has its own key in that project, written to its own Secret
Manager as version 2 (version 1 remains the dummy placeholder).

| env | key | secret | registry row |
|---|---|---|---|
| dev | `maps-grounding-dev` | v2 | seeded |
| test | `maps-grounding-test` | v2 | seeded |
| prod | — | v1 dummy only | — |

Verified live, not just applied: a real MCP `initialize` + `tools/list` against
both keys succeeds, the dev and test key strings are distinct (so one env is
revocable without the other), and the same key against the Gemini API is
`PERMISSION_DENIED`.

**Two gotchas that cost an apply each.** `apikeys.googleapis.com` must be
enabled on the maps project as well as `mapstools` — the first is the management
API terraform needs to create a key at all, and omitting it fails with a 403
that reads like a permissions problem. And `GCP_PROJECT` in the shell shadows
the seed script's target (`gotcha_gcp_project_env_shadow`); the script's own
guard caught it.

Prod is wired in `environments/prod/main.tf` but unapplied — the trigger runs
`environments/${BRANCH_NAME}`, and `origin/prod` does not contain the module.
To take prod live: merge to the `prod` branch, then
`GCP_PROJECT=your-project-id-prod … seed_mcp_servers.py --env prod`.

<details><summary>Original plan, before the project existed</summary>

Placeholder secrets `MAPS_GROUNDING_API_KEY=dummy_value` exist in all three env
projects (created 2026-08-11). They are what keeps the Cloud Run deploy green
between this code landing and a real key existing — Cloud Run rejects a revision
referencing a missing secret, which failed the dev backend deploy once before the
placeholders were in place.

Terraform is written and validated in `sunholo/multivac-aitana` (commit
`880e501`, **committed but unpushed**): a new `modules/maps_grounding_key`
invoked once per env, creating three API keys in a dedicated `your-maps-project`
project and writing each into its env's Secret Manager as a new secret version.
Restrictions are `api_targets = mapstools.googleapis.com` only, plus
`prevent_destroy` and `ignore_changes = [project]` — the two API-key incidents
(2026-03-28 abuse, 2026-04-21 destroy/recreate) are cited in the module README.
No referrer or IP restrictions, for reasons documented there.

**Blocked on a manual step.** The `your-maps-project` project does not exist and cannot
be created by terraform or by an agent: this repo models projects as `data`
sources only, and the folder cascade grants no `projectCreator` or billing role.
`owner@yourcompany.com` gets `PERMISSION_DENIED` on `resourcemanager.projects.create` for
folder `YOUR-FOLDER-ID`. Needs org credentials:

```bash
gcloud projects create your-maps-project --folder=YOUR-FOLDER-ID --name="Aitana Maps"
gcloud billing projects link your-maps-project --billing-account=YOUR-BILLING-ACCOUNT
gcloud services enable mapstools.googleapis.com --project=your-maps-project
```

The folder matters — inside `YOUR-FOLDER-ID` the terraform SA inherits
`serviceusage.apiKeysAdmin` and `secretmanager.admin` from the bootstrap cascade,
so no manual IAM grant follows.

Then push terraform to `dev`, then `test` (the trigger applies
`environments/${BRANCH_NAME}`, so each branch applies only its own env). Prod is
wired but deliberately held. Finally
`uv run python scripts/seed_mcp_servers.py --env dev` per env — Firestore state
never promotes with code (issue #14).

</details>

### M4 — attribution ✅ Done 2026-08-12

[`adk/a2ui_maps_render.py`](../../../backend/adk/a2ui_maps_render.py) renders the
attribution links onto a `maps_sources` surface with `kind: "sources"`, which
routes to the existing `SourcesArtefactTab`. The payload's `attribution` object
is `{title, url}` — exactly the `{title, uri}` that tab already renders as
clickable links — so this cost **zero frontend work** and adds no bespoke React
per tool (CLAUDE.md #7).

Two design points worth keeping:

- **The extractor walks the payload** rather than encoding field paths.
  Attribution sits at a different depth per tool (nested in `places[]`, nested in
  `routes[]`, top-level for weather), and the two undocumented tools are
  unguessable. One rule covers all five and survives nesting changes.
- **Titles pass through verbatim.** Google returns `"SolarCentric B.V. - Google
  Maps"`, and the guidelines forbid altering the "Google Maps" wording. Tidying
  the suffix away would delete the attribution the licence requires.

Its own surface, not `web_sources`: a turn can use both web search and Maps, and
a shared surfaceId would let one overwrite the other's citations.

18 tests pin the real captured payloads. They matter more than usual — if
Grounding Lite changes shape the transform returns `None`, nothing renders, and
an unattributed answer ships with nothing else going red.

### M5 — maps-assistant skill ✅ Done 2026-08-12 (dev + test)

[`skills/templates/maps-assistant/SKILL.md`](../../../backend/skills/templates/maps-assistant/SKILL.md)
— "Maps & Places", `model: pro`, declaring `maps-grounding-lite`.

**Tag-gated to `aitana-admin`** while prod's key is the dummy placeholder. The
prod *registry row* is seeded anyway, because the issue #14 seed check fails the
BUILD when a template declares a server the env cannot satisfy; the tag gate is
what keeps a customer off the 401 path in the meantime. Joins `DEMO_SKILL_NAMES`
for the same reason `knowledge-search` is there — it needs operator-only config,
so a fork must not seed it broken.

`pro` rather than a cheaper tier because Grounding Lite's arguments are typed
objects (`compute_routes` wants Waypoints, `lookup_weather` a nested `latLng`);
a weaker model gets these wrong and burns turns retrying.

**Verified live on both envs** — not jsdom, per CLAUDE.md's verification rule.
Real AG-UI streams show one `A2UI_SURFACE` CUSTOM event carrying
`surfaceId: maps_sources`, `artifact.kind: sources`, and the places'
verbatim-titled links: dev via `search_places` (5 sources), test via
`compute_routes` (1 source) — deliberately a different tool, so the
different-depth extraction is proven on two real shapes rather than one.

Shipped to test as **v6.27.0**, opened to ONE in **v6.28.0**.

### M5a — available to ONE ✅ 2026-08-12

ONE's front door was blind to Maps for **two** reasons, and both had to change:
`maps-assistant` was tag-gated to `aitana-admin`, and it was absent from
`one-assistant`'s `delegation.allow`. Delegate targets are **access-filtered**, so
the allow entry alone would still have resolved to nothing for a ONE user —
which is exactly why direct invocation worked while delegation did not.

**Prod is protected by the second gate, not by tags.** `enabled_skills` on
`clients/acme-energy.example` is per-env runtime data that does not ride a deploy.
`maps-assistant` is on that allowlist for dev and test only; prod's list is
non-null and omits it, so the skill is invisible to ONE there whatever the tags
say. That split is the point — a skill's ACCESS ships with the code while its
per-customer EXPOSURE stays an env-by-env decision, and tags are shared across
envs so they cannot express "test yes, prod no".

### The bug only a real run could find

The first delegated run asked for a drive time **and** the weather, so two Maps
tools fired in one turn. Both render to the same `maps_sources` surface, so the
second `updateDataModel` replaced the first: the weather citation appeared and
**the route's Google Maps link silently vanished**, while the route answer stayed
on screen. An unattributed output — the precise state the licence forbids — and
nothing would have gone red. Twenty-four unit tests were green throughout.

Fixed by accumulating each call's attributions onto a session-scoped list,
deduped by uri, capped at 30 keeping the most recent. The state key is
deliberately **unprefixed**: `app:` is one global odometer across sessions
(issue #38) and `user:` would leak one chat's citations into another.

This is the second time in this doc that a real run beat the test suite — worth
remembering next time a render change looks obviously correct.

### Two deploy traps this cost (both now fixed)

1. **The secret must be mounted on the backend SIDECAR.**
   `backend/cloudbuild.yaml` deploys the standalone IAM-protected
   `platform-backend`; chat is served by the backend sidecar inside
   `platform-frontend`, deployed by the ROOT `cloudbuild.yaml`. Mounting it on
   one does not mount it on the other, and the skill 500'd until both had it.
   The `${VAR}` resolver made this loud — it logged the exact missing variable
   and the mount command instead of dialling Google with a placeholder.
2. **`${...}` in `cloudbuild.yaml` is parsed even inside a shell comment.** The
   comment explaining trap 1 contained a literal `${MAPS_GROUNDING_API_KEY}`,
   which failed the whole build config with "not a valid built-in substitution"
   *before any step ran* — producing a build with no trigger name and no logs,
   so nothing deployed and the previous revision kept serving. Escape as `$$`.

### M6 — a map view (not scoped)

Deliberately deferred. Grounding Lite has no widget token (that is the native
Vertex path we cannot use) and the A2UI Basic catalog has no map component, so a
real map needs either the already-deployed `mcp-ext-apps-map` MCP App fed with
the `location` lat/lngs, or a new static artefact. The latter would need a
**second, referrer-restricted browser key** — never reuse the server key, which
has no referrer restriction by design and would be the 2026-03-28 shape again.

## Open Questions

1. **Which skill wants this first?** The capability is speculative until one
   does. Weather-at-site and drive-time-between-sites both plausibly serve the
   ONE energy-asset work, but neither was requested at the UAT.
2. **English-only.** Grounding Lite supports English prompts and responses only.
   Aitana's primary users are Spanish-speaking; a Spanish-language skill calling
   these tools needs a deliberate answer on which language the tool call is made
   in and how the result is rendered back.
3. **Does `search_places` count as egress of customer content?** A place query
   built from a customer document's site address leaves the GCP edge for Maps
   Platform. Per [privacy boundary](../../../CLAUDE.md), that needs an explicit
   justification before a skill over confidential documents is given this tool.
