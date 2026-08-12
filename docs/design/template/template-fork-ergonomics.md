# Template Fork Ergonomics

**Status**: Largely implemented in platform (Sprint FORK-ERGO-A, 2026-06-05; +G44 docs-only follow-up, 2026-06-07) — **template sync pending**. 15 of 18 items shipped or pre-existed (#1, #2, #3, #4, #11, #12, G15, G16, G17, G18, G19, G20, G23, G39, G44). G13 (deployed-fork bootstrap), G14 (region-aware Vertex), G21 (role discriminator) deferred to FORK-ERGO-B.  
**Priority**: P0 (was P1 — bumped after gde-ap-agent fork experience)  
**Estimated**: 5.5d planned; ~3h Sprint A actual (most items were one-line or audit-already-shipped); +30min G44 (docs only)  
**Scope**: Backend + Frontend + CLI + Config + Cloud Build + Bootstrap scripts + ops docs  
**Dependencies**: None  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-07  
**Source items**: #1 #2 #3 #4 #11 #12 (CPH Uni AIPLA upstream feedback — **all 45 of that log's entries triaged 2026-07-29, see the section directly below**); G13–G22 (gde-ap-agent fork, 2026-06-02, ailang msg `8e82510d`); G23 (downstream fork user, 2026-06-03 — sanitize/docs asymmetry); G39 (gde-ap-agent fork A2A discovery debug, 2026-06-05 — well-known proxy gap on Cloud Run multi-container deploys); G44 (gde-ap-agent fork dual-cloudbuild investigation, 2026-06-07 — deployment-model choice not discoverable, fork ran 6 months with failing standalone-backend trigger)

## AIPLA feedback-log triage (2026-07-29)

The CPH Uni AIPLA fork maintains a running friction log at
`cphu-aipla-app/docs/upstream-feedback.md` — **45 entries** as of 2026-07-29.
Until now only **#1–4, #11, #12** had ever been ingested here (see *Source
items* above). The rest were never triaged upstream.

All 45 were checked against this repo @ `44ebdff` and the fork's log annotated
in place. Outcome:

| Status | Count | Entries |
|---|---|---|
| ✅ Already fixed here | 41 | 1–14, 16–27, 30–44 (10 closed by sprint FORK-FEEDBACK-CLOSEOUT, 2026-07-29) |
| 🟡 Partially fixed | 2 | 15, 45 |
| ❌ Still open | 0 | — |
| ⚪ No action needed | 2 | 28, 29 |

> **Triage corrections (2026-07-29).** Three entries were first recorded wrongly,
> all for the same reason: the check greped code but never read
> [`docs/design/template/SEQUENCE.md`](SEQUENCE.md), where an earlier sprint
> series had already closed several items. **#17** (`/gcs_config` dead mount)
> and **#18** (silently-dropped `NEXT_PUBLIC_*` build args) both shipped
> 2026-06-05 in `template-dx-hardening.md`. And #32 below.
>
> The reverse also happened: that same index claimed **#16** and **#23** were
> shipped when they are not — #23's citation is the AIPLA fork's own commit
> hash copied across. Both index rows are now corrected. **Trust neither the
> index nor a single grep — verify against the code that owns the behaviour.**
>
> #32 was first recorded as open; that was
> wrong. The invariant is implemented in `backend/adk/agui.py`
> (`terminal_event_yielded`), not in the SSE wrapper where the first check
> looked — and it is stronger than the reported fix: it drops any event after
> *either* terminal and logs `agui_terminal_dedup`. Caught when M1 implementation
> started and found the code already there. Lesson: grep the layer that *owns*
> the invariant, not the layer the reporter happened to name.

**Process finding — this log was not being read.** ~22 of the 29 "fixed"
entries were fixed *independently*: the same bug found twice, months apart.
The clearest case is their **#37** (ADK `app:`-prefixed state keys are
application-global, so a per-session turn counter becomes one global odometer).
They documented it on 2026-06-23; we hit the identical bug and fixed it on
2026-07-28 as issue [#38](https://github.com/sunholo-data/ai-protocol-platform/issues/38) /
commit `4999307`. Reading their log would have pre-empted it. **Re-triage this
log before every template refresh** — it is the only systematic downstream
signal we get.

### Open items worth adopting, ranked

| Their # | Item | Why it matters upstream |
|---|---|---|
| **#39** | Privileged tool *results* are mirrored onto the client SSE stream | Generic confidentiality hole — any fork with a privileged server-tool result and a lower-trust audience leaks it to devtools. Directly in tension with this repo's own confidential-content rules. Highest severity on the list |
| **#16** | Anon-group state is in-process; `group_id_auth.py` still ships the never-landed Firestore TODO | Every fork rediscovers the `min-instances=1` workaround, defeating serverless. They have a working implementation to port |
| **#36** | Deploy trigger is not gated on CI | Red CI still ships here too — same defect, same fix |
| **#42** | Startup project guard is brand-anchored AND fail-open | `fast_api_app.py:55` `_expected_prefix = "your-project-id"`, warn-only. The 2026-07-29 sanitize pass de-brands it for the template but leaves the design flaw: warns when correct, silent when wrong |
| **#35** | No Vertex-semantics session test double | `InMemorySessionService` lets any uid read any session, so ownership regressions are invisible to CI. The test double is the valuable half, independent of their migration shim |
| **#23, #44** | Chat viewport flex chain; `ChatMarkdown` `components` identity | Two small frontend fixes. #44 remounts the entire rendered markdown subtree on any parent re-render |
| **#38** | Artefacts emit `structuredContent` only, no `content` | Artefacts are unusable in any MCP host but ours. Also covers their `window.openai` host-detection addendum |
| **#17, #18, #33, #34, #15** | Dead `/gcs_config` mount; Dockerfile ARG allowlist; dual-audience auth helpers; API discoverability | Structural cleanups, lower urgency |

## Implementation Status (Sprint FORK-ERGO-A, 2026-06-05)

| Item | Status | Files / notes |
|------|--------|---------------|
| #1 `seed_skills.py` closed-set DISPLAY_NAMES dict | ✅ Pre-existing | Dict already removed; frontmatter read via SKILL.md metadata |
| #2 `seed_skills.py` GCP project pin | ✅ Pre-existing | `PLATFORM_SEED_PROJECT` env-var fallback in place |
| #3 `PLATFORM_OWNER_EMAIL` fail-loud | ✅ Pre-existing | `_resolve_owner_email()` raises in non-LOCAL_MODE when unset |
| #4 CLI hardcoded URLs → config.yaml | ✅ Pre-existing | `cli/aiplatform/config.yaml` loaded at startup |
| #11a `aitana://` URI scheme | ✅ Pre-existing | InlineCitation uses `branding.CITATION_SCHEME` constant |
| **#11b `__aitanaTransport` field name** | ✅ Shipped 2026-06-05 | 4 callsites now use `branding.TRANSPORT_FIELD` (`mcpClient.ts` × 3 + `dev/mcp-apps/passive/page.tsx`) |
| #12 `_MCP_SANDBOX_URL` Aitana default | ⚠️ **Claim was wrong — fixed 2026-07-29** | "Defaults to empty string" described the CODE default (`NEXT_PUBLIC_MCP_SANDBOX_URL`), not the `cloudbuild.yaml` substitution — which still carried a LIVE Aitana sandbox URL and shipped it to every fork. Caught by the AIPLA fork's own re-triage, not by us. The sanitizer now blanks it (documented intent: MCP Apps degrade gracefully rather than pointing at someone else's infra), and a regex neutralises ALL live `*.a.run.app` hosts — the earlier de-branding renamed the service but left the project hash, so 15 files looked generic while still resolving to our infrastructure |
| **G13 deployed-fork bootstrap** | ✅ Scripts shipped 2026-07-29 — ⚠️ **not yet run against real GCP** | The four sub-scripts now exist: [`create-artifact-bucket.sh`](../../../scripts/create-artifact-bucket.sh), [`create-agent-engine.sh`](../../../scripts/create-agent-engine.sh), [`create-search-datastore.sh`](../../../scripts/create-search-datastore.sh), [`deploy-mcp-sandbox.sh`](../../../scripts/deploy-mcp-sandbox.sh), and `bootstrap-gcp-project.sh` step 5 points at all four. All are idempotent and validate arguments locally (`bash -n` + shellcheck clean, arg/help paths exercised). **The GCP API calls themselves are unverified** — they were written from the gde-ap-agent failure reports, not from a live run. Verify against a throwaway project before relying on them |
| G14 region-aware Vertex Agent Engine | ⏳ Deferred to FORK-ERGO-B | Needs real cross-region test |
| **G15 bare-id expansion** | ✅ Shipped 2026-06-05 | New [`backend/tools/resource_ids.py`](../../../backend/tools/resource_ids.py) — `resolve_resource_id("vertex_datastore", value)` expands bare ids to full resource paths; full paths pass through unchanged. Wired in [`backend/adk/agent.py:218`](../../../backend/adk/agent.py#L218). +7 tests in [`test_resource_ids.py`](../../../backend/tests/unit/test_resource_ids.py) |
| **G16 idempotent seeder (purge + refresh)** | ✅ Shipped 2026-06-05 | [`platform_seed.py`](../../../backend/admin/platform_seed.py) — `_purge_stale_owner_skills()` reads `PLATFORM_PREVIOUS_OWNER_UIDS` and deletes; existing skills now refresh via `update_skill()` instead of skip. `SeedSummary.{purged, refreshed}` counters. +4 tests |
| **G17 demo skills opt-in flag** | ✅ Shipped 2026-06-05 | `DEMO_SKILL_NAMES` constant + `_include_demo_skills()` reads `_INCLUDE_DEMO_SKILLS` (default `true` for platform; sanitize flips to `false` for template). Both [`cloudbuild.yaml`](../../../cloudbuild.yaml) + [`backend/cloudbuild.yaml`](../../../backend/cloudbuild.yaml) wire it through. +4 tests |
| **G18 cloudbuild `^\|^` delimiter** | ✅ Shipped 2026-06-05 | `ADMIN_SEED_ALLOWED_SAS` (the comma-bearing SA list) uses `^\|^` delimiter override in both cloudbuild.yaml files. Authoring rule documented as comment |
| G19 sub-agent name resolution | ✅ Pre-existing | `get_skill(sub_id)` already resolves by name |
| **G20 `process.env.X ?? "default"` → `\|\|`** | ✅ Shipped 2026-06-05 | 7 callsites converted: `app/page.tsx`, `api/proxy/[...path]/route.ts`, `.well-known/agent.json/route.ts`, `api/health/route.ts`, `api/proxy/health/route.ts`, `lib/anonymousGroupAuth.ts`, `lib/branding.ts`, `lib/localMode.ts`. Each carries an inline G20 comment explaining why |
| G21 `role: hub\|specialist` discriminator | ⏳ Deferred to FORK-ERGO-B | Touches sub-agent UX + routing — needs a real design pass |
| **G23 sanitize/docs asymmetry (Part B)** | ✅ Shipped 2026-06-05 | [`sanitize-for-template.sh`](../../../scripts/sanitize-for-template.sh) now deletes the 4 dead-link docs (`auth-smoke-testing.md`, `dev-accounts.md`, `agent-factory-smoke.md`, `env-promotion-audit.md`) and patches `smoke-deployed.sh::smoke_auth()` to no-op with a clear "not available in template fork" message. **Part A shipped 2026-07-29**: new [`backend/scripts/_smoke_config.py`](../../../backend/scripts/_smoke_config.py) makes `_env.py` optional — config resolves from flags → named env (when `_env.py` exists) → `NEXT_PUBLIC_FIREBASE_*` env vars → `frontend/.env.local`, and the throwaway principal is parametrized (`SMOKE_TEST_EMAIL` / `SMOKE_GROUP_TAGS`, with this deployment's values moved into the private `_env.py` as `SMOKE_IDENTITY`). `whoami_smoke.py`, `verify_rules.py`, `test_whoami_deployed.py` and the three ops docs (`auth-smoke-testing.md`, `dev-accounts.md`, `agent-factory-smoke.md`) now SHIP; the sanitize-time `smoke_auth()` neutering was deleted in favour of the shipped script detecting `_env.py`'s absence itself. +14 tests in [`test_smoke_config.py`](../../../backend/tests/unit/test_smoke_config.py) covering both modes. Only `env-promotion-audit.md` stays excluded (internal sitrep, not script docs) |
| **G39 `/.well-known/agent.json` Next-ingress proxy** | ✅ Pre-existing (shipped Sprint A) | [`frontend/src/app/.well-known/agent.json/route.ts`](../../../frontend/src/app/.well-known/agent.json/route.ts) |
| **G44 dual-cloudbuild deployment-model discoverability** | ✅ Shipped 2026-06-07 (docs-only) | Preamble rewrite in [`backend/cloudbuild.yaml`](../../../backend/cloudbuild.yaml) (25-line "DELETE IF…" block listing the four use cases the standalone service exists for) + new [`docs/ops/deployment-models.md`](../../ops/deployment-models.md) (TL;DR table, model A vs B, full removal recipe, symptoms-of-wrong-choice table). No code change, zero test impact. Sanitize pipeline doesn't need changes — both files ship as-is to forks |

**Validation:** Backend 1386/1386 (+15 net-new this sprint), frontend 555/555 (no net-new — G20/#11b runtime changes covered by existing tests), `make lint` + `npm run quality:check` all clean. G44 is docs-only; no test impact.

**Deferred to Sprint FORK-ERGO-B** (the deeper / riskier items):
- **G13** Deployed-fork bootstrap: needs `create-agent-engine.sh`, `create-artifact-bucket.sh`, `create-search-datastore.sh`, `deploy-mcp-sandbox.sh` + real GCP testing.
- **G14** Region-aware Vertex Agent Engine wiring: needs parsing `AGENT_ENGINE_ID` for its location segment, applying to both `VertexAiSessionService` + `VertexAiMemoryBankService`, AND a cross-region smoke test.
- **G21** `role: hub|specialist` discriminator: touches SkillConfig schema, agent factory routing, frontend sub-agent rendering, and the new structured-input endpoint mentioned in the design doc.

## Problem Statement

Every downstream fork of `sunholo-data/ai-protocol-platform` hits a cluster of friction
points caused by Aitana-specific values baked into the template's code. A fork can't use
the CLI seeder, must manually override the owner email, runs with an MCP sandbox URL
pointing at Aitana's infrastructure, and sees `aitana://` hardwired into protocol
identifiers that cannot be rebranded without touching source.

**Current State:**

- `backend/scripts/seed_skills.py` has a hardcoded `DISPLAY_NAMES` / `TAGS` / `INITIAL_MESSAGES` dict listing only the five inherited demo skills. Adding a new skill template means it seeds with falsy defaults (item #1).
- Same file pins the GCP project via `pin_project_for_env("dev")`, making the seeder unusable in any project other than `your-project-id` (item #2).
- `backend/admin/platform_seed.py` defaults `PLATFORM_OWNER_EMAIL` to `platform@yourcompany.com`; a fork that forgets to set the env var ships skills owned by Aitana (item #3).
- The CLI package, binary, and per-env default URLs all hardcode Aitana branding — `aiplatform`, `yourcompany.com`, `your-project-id-*` (item #4).
- "Aitana" appears in dev-page titles, doc comments, a `__aitanaTransport` internal field, the `aitana://` citation URI scheme, and an `aitana skill create` CLI example (item #11).
- `_MCP_SANDBOX_URL` defaults to `https://your-service-url.example/sandbox.html` — a live Aitana Cloud Run service (item #12).
- A fresh fork cannot reach a working `/chat` after `git push` without four undocumented one-time bootstrap steps (Agent Engine, ADK artifact bucket, AI Search datastore, MCP sandbox deploy) — each surfaced as a production 4xx/5xx in the gde-ap-agent fork (items G13a–G13d).
- `VertexAiSessionService` is built with `GOOGLE_CLOUD_LOCATION` even though Agent Engine often lives in a different region (e.g. `us-central1` while Cloud Run is `europe-west1`), causing 404s on every chat-start (item G14).
- SKILL.md `toolConfigs` accept bare ids (`datastore_id: ds-ap-vendors`, `bucket: demo`) but the runtime forwards them un-expanded; Vertex rejects them (item G15).
- A fork that rotates `PLATFORM_OWNER_UID` keeps the old Aitana-owned skill rows in Firestore — the seeder is create-if-missing only (item G16).
- Seven default demo skills ship opt-out-by-deletion; a fork has to delete eight files to ship a clean bundle (item G17).
- `cloudbuild.yaml`'s `--set-env-vars` uses comma as the kv-separator, so any list-valued substitution (`ADMIN_SEED_ALLOWED_SAS`, `ALLOWED_HOST_ORIGINS`) silently corrupts the deploy unless the `^|^` / `^@^` delimiter override is used everywhere (item G18).
- Sub-agent wiring forces forks to know generated skill UUIDs even though templates can only know names (item G19).
- `process.env.X ?? "default"` doesn't fall back on the empty string a container env returns when a var is set-but-empty (item G20).
- No `role: "hub" | "specialist"` discriminator on skills: every skill renders as a peer chat tab, even when its SKILL.md assumes orchestrator-provided context (item G21).
- Net-new UI surfaces (GCS browser, Audit View, doc-panel view modes, sidebar accordion, data-driven MCP App widgets) had to be invented in the fork because the template ships none (item G22).
- The sanitize pipeline strips `backend/scripts/whoami_smoke.py`, `backend/scripts/verify_rules.py`, `backend/scripts/_env.py`, and `backend/tests/integration/test_whoami_deployed.py` from forks (they import hardcoded Firebase web keys + reference a hardcoded `whoami-test@yourcompany.test` user), **but does not strip the docs that reference them** — six surviving docs and `scripts/smoke-deployed.sh` send fork users to dead files (item G23).

**Impact:**

- Any fork that relies on the command-line seeder (`seed_skills.py`) gets broken or Aitana-branded output.
- A fork that forgets `PLATFORM_OWNER_EMAIL` ships with skills that reference Aitana's identity.
- The `aitana://` URI is a load-bearing protocol identifier that shows up in `InlineCitation`, making it visible to users as a brand marker.
- The MCP sandbox URL ships broken-by-default: it works only while Aitana keeps that service running and only Aitana-hosted content can use it.

## Goals

**Primary Goal:** Any downstream fork should be able to run the template out-of-the-box
without patching Aitana-specific values, and should get loud errors (not silent wrong
defaults) when required configuration is missing.

**Success Metrics:**
- `seed_skills.py` works correctly for a skill not in the original five without touching the dict.
- `PLATFORM_OWNER_EMAIL` unset in a non-LOCAL_MODE env causes a startup failure with a clear message, not a silent wrong owner.
- A fork that sets `PLATFORM_SEED_PROJECT` in env can run the CLI seeder against its own GCP project.
- `_MCP_SANDBOX_URL` substitution defaults to an empty string; if no override is set the feature degrades gracefully (MCP Apps disabled, not misconfigured).
- The `aitana://` URI scheme and `__aitanaTransport` field name are sourced from a single constant in `branding.ts` / `branding.py` so a fork can rebrand in one file.

**Non-Goals:**
- Full rebrand automation (scaffolding, search-and-replace tooling) — that is a separate template-init concern.
- Changing the `aiplatform` binary name in the Aitana product itself — the rename only affects how the template packages the CLI.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No request-path changes |
| 2 | EARNED TRUST | 0 | |
| 3 | SKILLS, NOT FEATURES | +1 | Skills become more portable across forks |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | |
| 5 | GRACEFUL DEGRADATION | +1 | Fail-loud > silent wrong default; MCP sandbox degrades to disabled |
| 6 | PROTOCOL OVER CUSTOM | +1 | Removing brand-custom URI hardcoding |
| 7 | API FIRST | 0 | |
| 8 | OBSERVABLE BY DEFAULT | +1 | Startup validation surfaces mis-config early |
| 9 | SECURE BY CONSTRUCTION | 0 | |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+4** | Meets threshold |

## Design

### Item #1 — Seed skills from SKILL.md frontmatter

**File:** `backend/scripts/seed_skills.py`

Replace the `DISPLAY_NAMES` / `TAGS` / `INITIAL_MESSAGES` dicts with a frontmatter read:

```python
# Before (closed dict)
DISPLAY_NAMES = {"ai-search": "AI Search", "code-assistant": "Code Assistant", ...}

# After (read from SKILL.md metadata)
import yaml, pathlib

def _read_skill_md_meta(skill_dir: pathlib.Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {}
    text = skill_md.read_text()
    # Extract YAML frontmatter between --- fences
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}
```

`displayName`, `tags`, and `initialMessage` are already defined in SKILL.md frontmatter
(per the Agent Skills spec). The dict is then unnecessary; delete it.

### Item #2 — Replace project pin with env-var / ADC

**File:** `backend/scripts/seed_skills.py` line 36, `backend/scripts/_env.py`

```python
# Before
pin_project_for_env("dev")

# After — read from env, fall back to ADC's project
import os
project = os.environ.get("PLATFORM_SEED_PROJECT") or _gcp_project_from_adc()
if project:
    os.environ["GOOGLE_CLOUD_PROJECT"] = project
```

Add `PLATFORM_SEED_PROJECT` to the template's `.env.example` with a comment:
```
# Override the GCP project used by seed_skills.py.
# Defaults to GOOGLE_CLOUD_PROJECT / ADC project.
PLATFORM_SEED_PROJECT=
```

Remove `pin_project_for_env` from `_env.py` if its only callers are the seeder; otherwise
make it accept an explicit project arg.

### Item #3 — Fail-loud when PLATFORM_OWNER_EMAIL is unset

**File:** `backend/admin/platform_seed.py`

```python
# Before
PLATFORM_OWNER_EMAIL = os.environ.get("PLATFORM_OWNER_EMAIL", "platform@yourcompany.com")

# After
_raw = os.environ.get("PLATFORM_OWNER_EMAIL", "")
if not _raw:
    if LOCAL_MODE:
        _raw = f"platform@localhost"  # acceptable stub for local dev
    else:
        raise RuntimeError(
            "PLATFORM_OWNER_EMAIL is required in non-LOCAL_MODE. "
            "Set it to the platform admin email for this deployment."
        )
PLATFORM_OWNER_EMAIL = _raw
```

Add `PLATFORM_OWNER_EMAIL` to `cloudbuild.yaml` substitutions with a `_PLATFORM_OWNER_EMAIL`
substitution default of `""` so Cloud Build deploys still get a useful error rather than
silently using the Aitana fallback.

### Item #4 — CLI brand de-coupling

**Files:** `cli/pyproject.toml`, `cli/aiplatform/http.py`

The binary name stays `aiplatform` in Aitana's own product (it was renamed from `aitana`
once already). For the template, the fix is:

1. Move `_DEFAULT_URLS` from a hardcoded dict to a `cli/config.yaml` file read at startup,
   with `AIPLATFORM_API_URL_*` env-var overrides already documented:

```yaml
# cli/config.yaml (ships in the template; forks override the values)
environments:
  local:  http://localhost:1956
  dev:    ""   # set AIPLATFORM_API_URL_DEV
  test:   ""   # set AIPLATFORM_API_URL_TEST
  prod:   ""   # set AIPLATFORM_API_URL_PROD
```

2. Remove the docstring sentence that says *"Brand and backend remain Aitana Labs /
   your-project-id-*"* — it signals downstream is second-class.

3. Add a note to `cli/README.md` explaining how to rename the binary in a fork via
   `pyproject.toml` `[project.scripts]`.

### Item #11 — Aitana brand strings in code

**Files to change:**

| File | Current | Fix |
|------|---------|-----|
| `frontend/src/components/chat/InlineCitation.tsx:9,62` | `aitana://` URI scheme | Read from `branding.ts` constant `CITATION_SCHEME` |
| `frontend/src/app/dev/mcp-apps/passive/page.tsx:43` | `__aitanaTransport` field | Read from `branding.ts` constant `TRANSPORT_FIELD` |
| `frontend/src/app/skills/new/page.tsx:10` | `aitana skill create` CLI example | Change to generic `aiplatform skill create` |
| `frontend/src/app/dev/mcp-apps/page.tsx:12` | "Aitana MCP Apps" title | Generic "MCP Apps" |
| `frontend/src/app/dev/rich-media/page.tsx:80,125,127` | fixture filename + display text | Generic names |
| `frontend/src/types/skill.ts:5` | doc comment | Remove brand reference |

**`branding.ts` addition:**

```ts
// frontend/src/lib/branding.ts  (new file, forks edit this one place)
export const CITATION_SCHEME = process.env.NEXT_PUBLIC_CITATION_SCHEME ?? "inline-citation";
export const TRANSPORT_FIELD = `__${process.env.NEXT_PUBLIC_APP_SLUG ?? "platform"}Transport`;
```

The `aitana://` URI is the highest-priority fix because it appears in user-visible content
(citation chips in chat). The others are internal-only but still confusing for fork authors.

### Item #12 — MCP sandbox URL default

**File:** `cloudbuild.yaml`

```yaml
# Before
substitutions:
  _MCP_SANDBOX_URL: 'https://your-service-url.example/sandbox.html'

# After
substitutions:
  _MCP_SANDBOX_URL: ''   # Set to your deployed mcp-sandbox URL; blank = MCP Apps disabled
```

In `backend/config.py` / wherever `MCP_SANDBOX_URL` is consumed:

```python
MCP_SANDBOX_URL = os.environ.get("MCP_SANDBOX_URL", "")
MCP_APPS_ENABLED = bool(MCP_SANDBOX_URL)
```

Frontend reads `NEXT_PUBLIC_MCP_SANDBOX_URL`; if blank, `MCPAppToolCallRouter` renders a
"MCP Apps not configured" stub instead of a broken iframe.

---

## Findings from gde-ap-agent fork (2026-06-02)

Source: a Track-3 competition fork shipped end-to-end against `multivac-internal-dev`
between 2026-05-30 and 2026-06-02. Communicated upstream via `ailang messages send
ai-protocol-platform` (msg `8e82510d`) referencing the fork's local
[`docs/design/template/template-fork-ergonomics.md`](https://github.com/sunholo/gde-ap-agent/blob/main/docs/design/template/template-fork-ergonomics.md).

Each item below is either (a) a 4xx/5xx the fork hit in production, or (b) a feature the
template should ship that the fork had to invent. Commits cited so the upstream maintainer
can read the diff. Numbering is `G13`–`G22` to avoid colliding with CPH Uni's #13–#22 in
[SEQUENCE.md](SEQUENCE.md).

### Item G13 — Deployed-fork bootstrap automation

A clean fork cannot reach a working `/chat` after `git push` without four
one-time GCP resource creations. Each surfaced as a swallowed exception. The
template should either fold these into `bootstrap-gcp-project.sh` or ship a
dedicated `bootstrap-deployed-fork.sh`.

| Sub-item | Symptom | Bootstrap script gde-ap-agent had to write |
|---|---|---|
| G13a Vertex Agent Engine | `400 INVALID_ARGUMENT. Invalid ReasoningEngine resource name` on every chat — `AGENT_ENGINE_ID` Secret Manager secret held literal `"dummy_value"` and `fast_api_app.py:74` treats any truthy value as "use Vertex sessions" | `scripts/create-agent-engine.sh` (commits `ac8b00d`, `cd74b8c`) |
| G13b ADK artifact bucket | `404 ... The specified bucket does not exist` on every flow that touches `load_artifacts` — bucket name is conventionally `gs://<project>-artifacts` but it's never created | `scripts/create-artifact-bucket.sh` (commit `59a4428`) |
| G13c Vertex AI Search datastore | `400 ... datastore: Invalid Vertex AI datastore resource name`. Two bugs: datastore didn't exist *and* SKILL.md `datastore_id: ds-ap-vendors` was a bare id never expanded to the full path | `scripts/create-search-datastore.sh` + bare-id expansion (commit `20d4b29`) |
| G13d MCP sandbox deploy | `_MCP_SANDBOX_URL` defaults to Aitana's live URL (see #12), so MCP Apps point at someone else's infra or 404 | `scripts/deploy-mcp-sandbox.sh` + `scripts/verify-mcp-artefacts.sh` (commit `daecd4e`) |

**Fix:** Fold all four into `bootstrap-gcp-project.sh` (preferred — single command), or
ship `bootstrap-deployed-fork.sh` with the same four steps and a README "Deployed-Fork
Setup" section. The fork's "Common bootstrap failures" table (commit `cd74b8c`) should be
pulled in verbatim as the inverse-lookup index.

### Item G14 — Region-aware Vertex service construction

**File:** `backend/adk/sessions.py` (or wherever `get_session_service()` lives)

```python
# Before
service = VertexAiSessionService(
    project=GOOGLE_CLOUD_PROJECT,
    location=GOOGLE_CLOUD_LOCATION,  # europe-west1 (Cloud Run region)
)

# After — parse the region from AGENT_ENGINE_ID if it's a full resource name
def _extract_agent_engine_location(engine_id: str) -> str | None:
    m = re.match(r"projects/[^/]+/locations/([^/]+)/reasoningEngines/\d+", engine_id)
    return m.group(1) if m else None

agent_region = _extract_agent_engine_location(AGENT_ENGINE_ID) or GOOGLE_CLOUD_LOCATION
service = VertexAiSessionService(project=GOOGLE_CLOUD_PROJECT, location=agent_region)
```

**Why:** Agent Engine isn't available in every Cloud Run region. The fork's
`create-agent-engine.sh` defaults to `us-central1`; the Cloud Run service is in
`europe-west1`. Without parsing, the route hits
`projects/<p>/locations/europe-west1/reasoningEngines/<id>` and Vertex correctly 404s.
Apply identical fix to `VertexAiMemoryBankService`. Commit `1801cdb`.

**Bonus from same commit:** AG-UI route swallowed `RUN_ERROR` events and the audit view
rendered "specialist returned no tool calls" instead. Surface `RUN_ERROR` as a first-class
chip state in the template's protocol layer.

### Item G15 — Bare-id expansion for SKILL.md resource references

**File:** `backend/skills/skill_config.py` (or the toolConfig loader)

When a SKILL.md author writes the natural form:

```yaml
toolConfigs:
  ai_search:
    datastore_id: ds-ap-vendors           # bare id
```

the runtime should expand to the full resource path
`projects/<GOOGLE_CLOUD_PROJECT>/locations/<REGION>/collections/default_collection/dataStores/ds-ap-vendors`
before handing to `VertexAiSearchTool`. Today it forwards the bare string and Vertex
rejects it. Same pattern for:

- `bucket="demo"` sentinel in `import_gcs_object` (was not resolved before hitting GCS — `gs://demo/` crashed; fix commit `a2d6398`)
- any other `*_id` field in `toolConfigs` that has a canonical resource-path form

Suggest a single `resolve_resource_id(kind, value)` indirection invoked at config-load
time, where `kind ∈ {gcs_bucket, vertex_datastore, reasoning_engine, ...}`. Commit `20d4b29`.

### Item G16 — Idempotent skill-seeder ownership reconciliation

**File:** `backend/admin/platform_seed.py`

A fork that rotates `PLATFORM_OWNER_UID` (which it must — see #11 / G17) keeps the *old*
Aitana-platform-owned skill rows in Firestore until manually purged. The fork grew two
new seeder phases:

1. **Purge stale platform-owner skills** — query for skills whose `ownerUid` is a previous platform owner UID and delete them (commits `84a1e18`, `9fb5618`).
2. **Refresh template fields on existing skills** — when a SKILL.md changes (displayName, tags, initialMessage) the seeder must update the corresponding Firestore document, not just create-if-missing (commit `fa1d150`).

```python
def reconcile_platform_skills(db, current_owner_uid, previous_owner_uids):
    # Phase 1: purge skills owned by previous platform owners (no orderBy to
    # avoid composite-index requirements during first deploy)
    for prev in previous_owner_uids:
        for doc in db.collection("skills").where("ownerUid", "==", prev).stream():
            doc.reference.delete()

    # Phase 2: refresh template fields on current-owner skills
    for skill_dir in template_skill_dirs():
        meta = read_skill_md_frontmatter(skill_dir)
        ref = db.collection("skills").document(meta["name"])
        ref.set(meta, merge=True)  # merge so user-set fields survive
```

Document `PLATFORM_PREVIOUS_OWNER_UIDS` as a comma-separated env var.

### Item G17 — Default demo skills should be opt-in

**Files:** `backend/skills/templates/{code-assistant,data-extractor,document-analyst,general-assistant,web-researcher,workspace-demo,workspace-demo-interactive}/SKILL.md`

The fork deleted all seven in commit `a42b21f`. The template should ship them under
`backend/skills/templates/demo/` and gate seeding on a `_INCLUDE_DEMO_SKILLS` Cloud Build
substitution / env var (default `false`). Forks that want them flip one flag; forks that
don't, get a clean slate.

### Item G18 — Cloud Build env-var comma-escaping foot-gun

**File:** `cloudbuild.yaml`

`gcloud run deploy --set-env-vars KEY=VAL,KEY2=VAL2` uses comma as the kv separator. Any
*value* containing a comma (URL list, SA-allowlist) silently corrupts the deploy. The
fork hit this three times:

- `ADMIN_SEED_ALLOWED_SAS` (commits `9b76b0b`, `9466636`) — fixed with `^|^` delimiter override
- `ALLOWED_HOST_ORIGINS` (commit `4cfcaf9`) — fixed with `^@^` delimiter override

```yaml
# Before
- '--set-env-vars'
- 'ADMIN_SEED_ALLOWED_SAS=sa1@x.iam,sa2@x.iam,FOO=bar'

# After — gcloud delimiter-override syntax (see `gcloud topic escaping`)
- '--set-env-vars'
- '^|^ADMIN_SEED_ALLOWED_SAS=sa1@x.iam,sa2@x.iam|FOO=bar'
```

**Rule for the template:** always use `^|^` delimiter syntax in `cloudbuild.yaml`, even
when current values don't need it. A future single-value-with-comma silently breaks
otherwise. Roll into [template-cloudbuild-hardening.md](./template-cloudbuild-hardening.md).

**Related deploy fixes in the same cluster:**
- Explicit `--service-account` on `gcloud run deploy` (commit `48a04a4`)
- Seeder needs a cold-start sleep before its first Firestore write on a fresh service (commit `a95da1f`)

### Item G19 — Sub-agent resolution by skill name, not generated UUID

**File:** `backend/adk/agent.py`

Templates declare sub-agents by name:

```yaml
subSkills:
  - docparse
  - ap-validator
  - ap-poster
```

…but the skill processor receives the *generated UUID* of each skill row. A fork has no
way to write the UUID into its template. The fork added `skill_config.find_by_name()` +
a name-fallback in the sub-agent loop (commit `dca1323`). Upstream this so all forks can
rely on declarative-by-name `subSkills` together with the new `role` discriminator (G21)
without per-fork plumbing.

### Item G20 — Empty-string env-var fallback should use `||`, not `??`

**File:** `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` (and siblings)

```ts
// Before — fails when SANDBOX_PROXY_URL is "" (legitimately empty in container envs)
const url = process.env.SANDBOX_PROXY_URL ?? "http://localhost:8888";

// After
const url = process.env.SANDBOX_PROXY_URL || "http://localhost:8888";
```

The `??` nullish-coalescing operator does not fall back on the empty string that
`process.env.*` returns when an env var is set-but-empty (common in container envs that
pre-declare all variables). Commit `5be9ae2`. Grep the entire template for
`process\.env\.\w+\s*\?\?` and convert env-var defaults to `||`, or introduce a typed
`env(name, default)` helper that encodes this once.

### Item G21 — SkillConfig `role: "hub" | "specialist"` discriminator

**File:** `backend/skills/skill_config.py`, `frontend/src/lib/skillMeta.tsx`

The template renders every skill as a peer chat tab, even when the skill's SKILL.md
explicitly assumes orchestrator-provided context (e.g. *"You receive a structured invoice
from the extraction step"*). The fork built an entire Audit View UX around the missing
distinction (multi-agent-inspector-ux design doc in the fork repo, commit `4f9267f`).

Adding `role: "hub" | "specialist"` to SKILL.md frontmatter (default `"hub"` for backward
compat) lets the template:

- Route `/chat/<specialist-id>` to the parent hub when `role: specialist` and no `?devmode=1`
- Render specialists as inspector chips next to the hub tab rather than peer tabs
- Use the role to choose between free-text chat and structured-input forms

Pair with the new structured-invocation endpoint
(`POST /api/skill/{id}/structured`, `backend/skills/structured_invocation.py`,
commit `4f9267f`) — both belong in the template.

### Item G22 — Net-new UI surfaces this fork built that belong in the template

Not bugs — missing capabilities the fork shipped because any non-trivial demo will need
them. Suggest upstreaming each as a `?feature=…` toggle so the template stays minimal but
forks don't reinvent.

| Surface | Files (in fork) | Commit |
|---|---|---|
| GCS bucket browser (sidebar + backend `gcs_browser` tool + cloudbuild wiring for demo bucket) | `backend/tools/gcs_browser/` + `frontend/src/components/doc-browser/GCSFileBrowser.tsx` | `06262dc` + `bcb51ac` + `42b8fe6` |
| Multi-agent Audit View (chips + inspector panel + structured-input forms) | `frontend/src/components/audit/` | `4f9267f` + `e327dcd` + `e335483` |
| Doc panel view modes (side / focus / collapsed + drag resize) | `frontend/src/components/doc-browser/` | `feb4071` |
| Collapsible sidebar accordion w/ per-section toggles | `frontend/src/components/navigation/Sidebar*` | `19995af` |
| `ap-vendor-kg` style data-driven MCP App widget pattern | `infrastructure/mcp-sandbox/artefacts/ap-vendor-kg/index.html` | `4f9267f` |

These should land as a separate v6.X feature design doc rather than inside this ergonomics
doc — the scope is "what every demo needs", not "what every fork needs to not be broken".
Tracked as a follow-up; see SEQUENCE.md row.

### Item G44 — Dual `cloudbuild.yaml` deployment model not discoverable by forks

**Surfaced by:** gde-ap-agent fork during dual-cloudbuild investigation
(2026-06-07) — *"backend/cloudbuild.yaml targets a service that doesn't exist
in our project, and the trigger has been FAILing on every push to dev since
2026-06-05. The template gave us this file with no way to tell whether we
should keep it."*

**Symptom:** A fork that only serves a chat UI inherits **two** Cloud Build
files from the template:

- Root [`cloudbuild.yaml`](../../../cloudbuild.yaml) — deploys
  `platform-frontend` as a multi-container service (Next.js + FastAPI
  sidecar). This is the chat path.
- [`backend/cloudbuild.yaml`](../../../backend/cloudbuild.yaml) — deploys
  `platform-backend` as a standalone IAM-protected service. The platform
  uses it for channel webhooks (Telegram/email/WhatsApp), external A2A
  crawlers, and SA-to-SA Cloud Run callers without Firebase sessions.

For a fork that has none of those callers, the standalone service is pure
overhead — but **nothing in the template surfaces that question** at fork
time. The fork's options are:

1. Keep both files and accept failing builds + orphan triggers forever.
2. Reverse-engineer the platform's intent from comments inside the
   `backend/cloudbuild.yaml` (the only mention of "sidecar duplication" is
   buried at line 3 of an 8-line preamble).
3. Hope the upstream maintainer responds to an ailang message.

The gde-ap-agent fork went six months running option 1 before noticing CI was
red on every dev push.

**Root cause:** the template optimises for the platform's own deploys, which
exercise both models. The choice is invisible to a fork that only needs one.

**Fix — implemented 2026-06-07:** three concrete changes that surface the
choice without forcing it.

1. **Preamble rewrite in
   [backend/cloudbuild.yaml](../../../backend/cloudbuild.yaml)** — replace
   the existing 8-line header with a 25-line "DELETE THIS FILE IF…" block
   that lists the four use cases the standalone service exists for and
   points at the new ops doc for the removal recipe.
2. **New [docs/ops/deployment-models.md](../../ops/deployment-models.md)**
   — single page covering: TL;DR table, what each model is, why the default
   is single-service, when paired-services is justified, full removal recipe
   (delete file → disable trigger → delete service → strip Terraform refs),
   symptoms-of-wrong-choice table.
3. **This SEQUENCE.md entry** so the fix syncs to the public template on
   the next `aitana-template-publish` run.

**Why not just delete `backend/cloudbuild.yaml` from the template?** Because
the platform's own deploys depend on it (channel webhooks + external A2A +
SA callers are all real for the platform), and the sanitize pipeline can't
auto-detect which use cases a fork plans to enable. Surfacing the choice is
correct; eliminating one of the options is not.

**Template improvement summary:** every fork that doesn't run channels has
this gap silently. Documenting the choice up-front is one-time work; six
months of failing CI per fork is not.

### Item G39 — `/.well-known/agent.json` not proxied by Next ingress on multi-container Cloud Run

**Surfaced by:** gde-ap-agent fork during A2A discovery debugging (2026-06-05) —
*"a2a code worked all along; Cloud Run multi-container topology hides it."*

**Symptom:** A2A crawlers hitting the deployed URL with the RFC 8615 path
(`/.well-known/agent.json`) get Next.js's 404 page. The backend handler at
[backend/protocols/a2a.py](../../../backend/protocols/a2a.py) returns the agent
card correctly with the `extensions` field + `X-A2A-Extensions` negotiation
header — but the route is unreachable.

**Root cause:** Cloud Run multi-container topology:
- **Frontend container** (Next.js) owns the public ingress on port 8080. No
  `/.well-known/agent.json` route → returns 404.
- **Backend container** (FastAPI) has the route but listens only on
  `127.0.0.1:1956` as a sidecar — no external port.

The proxied path `/api/proxy/.well-known/agent.json` returns 200 ✓, but no
A2A crawler knows to look there. RFC 8615 mandates the unprefixed
`/.well-known/<suffix>` URI.

**Fix — implemented 2026-06-05:** One Next.js route handler at
[frontend/src/app/.well-known/agent.json/route.ts](../../../frontend/src/app/.well-known/agent.json/route.ts)
that proxies byte-for-byte and preserves:

1. `X-A2A-Extensions` **request** header → forwarded upstream (extension
   negotiation works)
2. `X-A2A-Extensions` + `Vary` **response** headers → echoed back unchanged
   (crawler sees the negotiated set)

Modeled on the existing `frontend/src/app/api/proxy/health/route.ts` pattern.
~50 LOC including comments.

**Smoke test (post-deploy):**
```bash
curl https://<deployed-url>/.well-known/agent.json \
  -H 'X-A2A-Extensions: a2ui-v0.9, a2ui-decoupled-pattern' -i
```

**Template improvement:** every fork that ships the multi-container Cloud Run
pattern has this gap silently. The route should ship in the template. Pair
with documentation pointing out that A2A discovery URIs live at
`/.well-known/<suffix>` (RFC 8615) and a Next ingress that doesn't proxy them
will silently fail at deploy time.

### Item G23 — Sanitize pipeline strips whoami-smoke code but leaves the docs

**Surfaced by:** Downstream fork users (2026-06-03) — *"the whoami smoke is excluded from
forks, but referenced in the documentation — it's confusing downstreamers."*

**Files stripped by `scripts/sanitize-for-template.sh` lines 85–90:**

- `backend/scripts/_env.py` (hardcoded Firebase web API keys → secret scanner flags)
- `backend/scripts/verify_rules.py`
- `backend/scripts/whoami_smoke.py`
- `backend/tests/integration/test_whoami_deployed.py`

**Files that survive the sanitize and still reference the stripped code:**

| File | Reference |
|------|-----------|
| `docs/ops/auth-smoke-testing.md` | Entire doc is "the whoami round-trip" — code blocks, links, troubleshooting all point at `whoami_smoke.py` |
| `docs/ops/dev-accounts.md` | `make smoke-auth`, link to `backend/scripts/whoami_smoke.py`, link to `test_whoami_deployed.py`, hardcoded `whoami-test@yourcompany.test` user |
| `docs/ops/agent-factory-smoke.md` | `from scripts.whoami_smoke import _ensure_user, _sign_in` recipe |
| `docs/ops/env-promotion-audit.md` | Pre-promotion checklist row + smoke commands reference `whoami_smoke.py` and `verify_rules` |
| `docs/design/v6.1.0/implemented/aiplatform-cli-selftest-sprint.md` | Sprint history references |
| `docs/design/v6.0.0/template-split-strategy.md` | Strategy doc references the smoke as fork-side infra |
| `scripts/smoke-deployed.sh:356` | Calls `uv run python scripts/whoami_smoke.py --env "$ENV"` — fails at runtime in any fork |

**Why the asymmetry:** the smoke scripts import `_env.py` which carries hardcoded
Firebase web keys (per [gotcha_firebase_auth_traps](../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/gotcha_firebase_auth_traps.md)
— "public" web keys still get abused for unauthorized API calls when Google APIs are
enabled in the same project). The sanitize pipeline excludes the *code* to keep secret
scanner clean, but the prose stayed.

**Fix — two-part:**

**Part A — Generalize and re-ship the whoami smoke.** Three changes make
`whoami_smoke.py` shippable:

1. Read Firebase config from env / `firebase-config.json` rather than the hardcoded
   `_env.py` dict. The Firebase Web API key per-env mapping already lives in
   `frontend/src/lib/firebase/` for the browser SDK — backend smokes should read the
   same source rather than maintaining a parallel one.
2. Parametrize the test user — `WHOAMI_SMOKE_USER` env var, default
   `smoke-test@<APP_DOMAIN>.test` where `APP_DOMAIN` comes from `branding.py` (see
   #11). No more `whoami-test@yourcompany.test` literal.
3. Keep `_env.py` excluded from forks (it has more than just the Firebase keys), but
   extract the per-env Firebase web-key lookup into a small `firebase_env.py` that
   reads `firebase-config.json` and ships.

After Part A, restore `whoami_smoke.py` + `verify_rules.py` + `test_whoami_deployed.py`
to the `_KEEP_PATHS` / non-deleted set in `sanitize-for-template.sh`. They now work
for any fork that has `firebase-config.json` populated.

**Part B — If Part A is too big for this sprint, sanitize the docs instead.** Either
extend `sanitize-for-template.sh` to delete the six surviving docs and the auth-smoke
section of `smoke-deployed.sh`, or replace them with a `docs/ops/auth-smoke-stub.md`
explaining "this used to be Aitana-internal — forks must implement their own
authenticated probe; here's the recipe." Recommend Part A — the smoke is exactly the
kind of fork-side infra a template should ship, and the dead-link state is worse than
no docs at all.

**Decide on Part A vs Part B during planning.** Default to Part A; fall back to Part B
only if Firebase-config-from-env turns out to require more than the 4h budget below.

### CLI Surface

No new commands. The config.yaml approach lets `aiplatform --env` work unchanged.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Replace DISPLAY_NAMES/TAGS/INITIAL_MESSAGES dicts with frontmatter read (#1) | 2h |
| 2 | Replace `pin_project_for_env` with `PLATFORM_SEED_PROJECT` env-var (#2) | 1h |
| 3 | Add startup validation for `PLATFORM_OWNER_EMAIL` (#3) | 1h |
| 4 | Move `_DEFAULT_URLS` to `cli/config.yaml`; remove brand docstring (#4) | 2h |
| 5 | Add `branding.ts` + wire `CITATION_SCHEME` + `TRANSPORT_FIELD` (#11) | 2h |
| 6 | Set `_MCP_SANDBOX_URL` default to `''`; add `MCP_APPS_ENABLED` guard (#12) | 1h |
| 7 | Fold the four deployed-fork bootstrap scripts into `bootstrap-gcp-project.sh` + add "Deployed-Fork Setup" + "Common bootstrap failures" docs section (G13) | 4h |
| 8 | Region-aware `VertexAiSessionService` + `VertexAiMemoryBankService` construction + surface `RUN_ERROR` in audit view (G14) | 2h |
| 9 | `resolve_resource_id(kind, value)` indirection for bare-id expansion (G15) | 2h |
| 10 | Idempotent seeder reconciliation (purge + refresh phases) + `PLATFORM_PREVIOUS_OWNER_UIDS` (G16) | 2h |
| 11 | Move seven default skills under `demo/`; gate on `_INCLUDE_DEMO_SKILLS` substitution (G17) | 1h |
| 12 | Audit `cloudbuild.yaml` to use `^|^` delimiter everywhere; explicit `--service-account`; cold-start sleep (G18) | 2h |
| 13 | Sub-agent resolution by name + `role` discriminator (G19, G21) + structured-input endpoint upstream | 4h |
| 14 | Grep template for `process.env.X ??` → `||`; add typed `env()` helper (G20) | 1h |
| 15 | Spin G22 net-new surfaces out into a separate v6.X design doc; do not block this sprint | 0h |
| 16 | G23 Part A: extract Firebase per-env keys to `firebase-config.json`-backed `firebase_env.py`; parametrize `WHOAMI_SMOKE_USER`; restore `whoami_smoke.py` + `verify_rules.py` + `test_whoami_deployed.py` to sanitize KEEP set; fix `scripts/smoke-deployed.sh` call site | 4h |
| 17 | Update tests for all items above | 4h |
| 18 | Update `docs/ops/` + `CLAUDE.md` + `.env.example` + bootstrap-failures table | 2h |

**Total: ~40h ≈ 5d** (matches new 5.5d estimate plus PR-review buffer).

## Testing Strategy

- **`test_seed_skills.py`** — add a fixture skill outside the five-skill list; assert display name, tags, and initial message are read correctly from its SKILL.md.
- **`test_platform_seed.py`** — assert startup raises `RuntimeError` when `PLATFORM_OWNER_EMAIL` is unset and `LOCAL_MODE=false`.
- **`test_branding.ts`** — snapshot test for `CITATION_SCHEME` default and env-override.
- **`test_mcp_apps_disabled.tsx`** — render `MCPAppToolCallRouter` with empty `MCP_SANDBOX_URL`; assert stub rendered, not broken iframe.
- Manual smoke: fork the template locally; run seeder without any Aitana-specific env vars; verify clean output.

## Success Criteria

- [ ] `seed_skills.py` with a sixth skill template correctly seeds display name, tags, and initial message from its SKILL.md frontmatter.
- [ ] `seed_skills.py` run with `PLATFORM_SEED_PROJECT=my-project` targets the correct GCP project.
- [ ] Backend startup in non-LOCAL_MODE with `PLATFORM_OWNER_EMAIL` unset exits with a useful error message.
- [ ] `_MCP_SANDBOX_URL` blank → `MCPAppToolCallRouter` renders a graceful "not configured" state.
- [ ] `InlineCitation` uses `CITATION_SCHEME` from `branding.ts`; no `aitana://` literal in the component.
- [ ] A clean fork can run `bootstrap-gcp-project.sh <project> <sa>` and reach a working `/chat` on first deploy without any of the four follow-up bootstrap scripts (G13).
- [ ] Backend with Agent Engine in `us-central1` and Cloud Run in `europe-west1` chats successfully (G14).
- [ ] SKILL.md with `datastore_id: <bare-id>` resolves to the full resource path automatically (G15).
- [ ] Rotating `PLATFORM_OWNER_UID` cleans up previously-owned skills on next deploy (G16).
- [ ] `_INCLUDE_DEMO_SKILLS=false` ships zero default skills; `=true` ships the seven demo skills (G17).
- [ ] Adding a comma-bearing value to any `cloudbuild.yaml` env var works without code changes (G18).
- [ ] `subSkills: [name, name]` resolves correctly in a fresh fork without name→UUID plumbing (G19).
- [ ] All instances of `process.env.X ??` for env-var defaults converted to `||` or `env()` helper (G20).
- [ ] SKILL.md with `role: specialist` is no longer reachable via `/chat/<skill-id>` peer URL without `?devmode=1` (G21).
- [ ] In a fresh fork, `./scripts/smoke-deployed.sh dev all auth` completes successfully without `ModuleNotFoundError` or missing-file errors (G23).
- [ ] In a fresh fork, every link and command in `docs/ops/auth-smoke-testing.md`, `dev-accounts.md`, `agent-factory-smoke.md`, and `env-promotion-audit.md` either works as-published or has been rewritten/removed by the sanitize pipeline — no dead `whoami_smoke.py`/`verify_rules.py` references survive (G23).
- [ ] All existing tests pass.

## Related Documents

- [local-dev-cli.md](../../v6.1.0/local-dev-cli.md)
- [aitana-template-publish skill](../../../.claude/skills/aitana-template-publish/SKILL.md)
- [template-cloudbuild-hardening.md](./template-cloudbuild-hardening.md) — pairs with G18 (delimiter syntax, cold-start sleep)
- [SEQUENCE.md](SEQUENCE.md)
- gde-ap-agent fork doc: `sunholo/gde-ap-agent` → `docs/design/template/template-fork-ergonomics.md` (source of G13–G22)
- ailang message `8e82510d` (2026-06-02) — original notification from the fork
- **G22 follow-up:** net-new UI surfaces (GCS browser, Audit View, doc-panel modes) should land as a separate v6.X design doc; not blocking this sprint
