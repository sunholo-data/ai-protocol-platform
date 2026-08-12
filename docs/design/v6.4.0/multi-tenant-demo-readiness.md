# ONE Demo Readiness — This Deployment Is Acme Energy

**Status**: Planned
**Priority**: P0 (commercial demo this week)
**Estimated**: 4 days (Tue 2026-06-09 → Fri 2026-06-12)
**Scope**: Fullstack (per-deployment branding via env vars + ONE skills + clause extraction + doc-compare workbench + ENTSO-E tool)
**Dependencies**:
- client-tenant-management (v6.3.0 ✅) — keeps the per-user bucket + skill filter; not used for branding
- branding.ts (frontend ✅) — extended to read env vars
- multi-surface rendering (v6.2.0 2.9 ✅)
- a2ui-surface-context (v6.2.0 2.10 ✅)
- fork-visual-demo-pullback (v6.4.0 4.1) — M1 workspace primitives + M4 workbench artefacts (in-flight, can run in parallel since the demo deadline is fixed)
**Created**: 2026-06-08
**Last Updated**: 2026-06-08 (refactored — per-deployment branding, existing Cloud Run service rebranded to ONE)

## Architectural Decision: This Deployment Is ONE

The existing `platform-frontend` Cloud Run service in `your-project-id` **becomes** Our New
Energy's deployment. No second Cloud Run service. No runtime tenant switching. No
`?tenant=` URL parameter. No `/api/branding/{domain}` endpoint.

**Rationale:**
- The "multi-tenant runtime branding" approach (an earlier draft of this doc) solved a problem
  Aitana doesn't have yet — there is no self-service SaaS where multiple companies sign up and
  each gets their own brand at the same URL.
- The actual product shape today is: **one deployment per customer brand**, same code, same image,
  different env-var-driven branding. v5 worked this way (one VAC per customer); the
  `tutoring-fork` fork pattern works this way; the public template at
  `sunholo-data/ai-protocol-platform` works this way (Sunholo defaults).
- ONE is the first commercial brand to land on this code. So this deployment, which the
  Aitana team currently uses for dev, gets rebranded to ONE.
- Aitana itself doesn't have a branded URL yet — when it needs one, it'll be a second deployment
  set up the same way (different env vars, different Cloud Run service).
- The template story stays clean: **`branding.ts` reads env vars with Sunholo defaults**; every
  fork rebrands by setting env vars in its `cloudbuild.yaml`. ONE is the first fork-by-config.

**What this preserves from the v6.3.0 multi-tenant work:**
- Per-user bucket resolution (`resolve_documents_bucket(user)` in `db/clients.py`) — ONE users still
  hit `multivac-acme-energy-bucket`; if an `@yourcompany.com` admin signs in to the ONE deployment, they hit
  the Aitana bucket (correct — admins debugging on the ONE deployment shouldn't accidentally write
  to ONE's bucket)
- Per-user skill filter (`ClientConfig.enabled_skills`) — defence-in-depth so the few admin domains
  that touch this deployment don't accidentally see ONE-internal-only skills (if any are marked
  that way later)
- `aiplatform client set` CLI from v6.3.0 — unchanged, still used to register ONE's bucket and
  enabled skills

## Problem Statement

Acme Energy (ONE) is a paying client with a v5 deployment at
`<local-path>` (Cloud Run service `acme-energy` in
`multivac-internal-dev`, running a Sunholo-era stack). The commercial conversation this week needs
a v6 demo that:

1. Looks like ONE's product (logo, name, copy — no "Sunholo" or "Aitana" visible)
2. Speaks PPA/PtX/BESS fluently using ONE's own vocabulary (PaP/PaN/BL/RtM, REDII/REDIII, RFNBO, SOEC/PEM/Alkaline)
3. Searches ONE's actual PPA contract corpus
4. Touches ONE's live ENTSO-E electricity-market data in `your-entsoe-project` BigQuery
5. Delivers the **"compare this PPA contract to this one"** workflow — ONE consultants' bread-and-butter task

A **code fork** (new repo, new image, new everything) would deliver this — but it fragments the
codebase and breaks the template thesis. The decision (2026-06-08, refined) is to **fork by
deployment-config**: same repo, same image, env-var-driven branding + Firestore-driven skill
registry. ONE is the first deployment-fork of the platform-template pattern.

**Current State:**
- `branding.ts` exports a static `BRANDING` object hardcoded to "Sunholo / AI Protocol Platform".
  The `CITATION_SCHEME` and `TRANSPORT_FIELD` exports in the same file already read
  `NEXT_PUBLIC_*` env vars — pattern exists, just hasn't been extended to the rest of `BRANDING`.
- ONE's PPA expertise prompt (v5 `prompt.txt`, ~80 lines of dense domain vocabulary) has not been
  ported to a v6 `SKILL.md`. The general-assistant skill cannot answer "what's the difference
  between PaP and PaN at hour-ahead settlement?" without hallucinating.
- **ONE has years of indexed PPA expertise** in `gs://multivac-acme-energy-bucket` (in the
  `multivac-acme-energy` GCP project) — 247 files under `PPAs/` (broken into `by clauses/`,
  `longform/`, `rtm/`, `termsheets/`, `cip_endesa_spain_by_clauses/`), 821 files under
  `documents/`, plus `Financing Contracts/`, `INFRAVIA/`, `Newsletters/`, `competitors/`,
  `cases/`. The same bucket is **indexed by an existing Vertex AI Search datastore** for semantic
  search (v5's `vac_config.yaml` references `vectorstore: vertex_ai_search` + `vector_name:
  one_generic`). v6 has not yet been pointed at either — `clients/acme-energy.example.documents_bucket`
  is unset, the v6 `ai_search` tool (via `VertexAiSearchTool`, already wired in
  `backend/tools/search_agent.py`) has no skill pointing at ONE's datastore, so neither the
  semantic RAG nor direct GCS reads work today.
- ENTSO-E data (`your-entsoe-project.entsoe.*`) is
  live and queryable but no ADK tool exposes it.
- The marketplace shows all generic skills (`web-researcher`, `code-assistant`,
  `general-assistant`) — none of which differentiate the product.
- The "compare two PPA contracts" workflow doesn't exist in any form. This is the
  highest-value ONE task and currently has no v6 surface.

**Impact:**
- **Demo blocker** — the existing deployment URL renders Sunholo branding to ONE viewers. The
  commercial conversation can't show that as ONE's product.
- **Template thesis** — if a "fork-by-config" deployment can't match a code fork on demo quality,
  every commercial client demands a code fork and the template/forks distinction collapses.
- **Forcing function for `fork-visual-demo-pullback`** — this sprint instantiates 4.1's generalised
  workbenches with a real client's content; gaps that surface here flow back into 4.1's
  generalisation rules.

## Goals

**Primary Goal:** Friday 2026-06-12 EOD, **any visitor** to the existing `platform-frontend`
Cloud Run URL sees Acme Energy branding from first paint (no auth required), and an authenticated
ONE user can ask a PPA-vocabulary question grounded in **ONE's real document corpus at
`gs://multivac-acme-energy-bucket`** (years of indexed PPAs, term sheets, and clause libraries),
see structured clause extraction with `block_id` citations, run side-by-side comparison of two real
ONE PPA contracts in a workbench, and pull live ENTSO-E price data — all on this same Cloud Run
deployment.

**Success Metrics:**
- ONE-branded landing renders for every visitor (logo, tagline, hero copy) from first paint,
  baked into the bundle at build time — zero runtime resolution latency
- Sunholo defaults stay in `branding.ts` so the public template at `sunholo-data/ai-protocol-platform`
  continues to ship Sunholo branding when refreshed via `aitana-template-publish`
- `one-ppa-expert` skill answers "summarise [contract X from PPAs/longform/]" with cited extracts
  from `gs://multivac-acme-energy-bucket/PPAs/`
- `extract_ppa_clauses(doc_id)` returns a typed `PpaClauses` object with `block_id` citations for
  every populated clause; renders as a Clause Extraction Card on the A2UI workspace surface
- `one-doc-compare` skill opens a `SideBySideDocViewer` workbench showing two contracts side-by-side
  with synchronized scroll + block-level diff highlights; Key Differences panel renders the
  agent-generated `commercial_implication` per diff; clicking a diff explains it in chat with both
  source `block_id` citations
- ENTSO-E ADK FunctionTool returns last 7d day-ahead prices for a given bidding zone in <2s, with
  source attribution to the BigQuery table; valuation step "what would this price-formula
  difference cost at DK1 prices?" composes `compare_ppa_contracts` + `entsoe_day_ahead_prices` end-to-end
- Skill marketplace shows exactly the 3 ONE-enabled skills to ONE users, hides the generic ones
- `aiplatform demo verify --tenant acme-energy.example` runs green
- Demo rehearsal completes the 5-minute walkthrough without engineering intervention by EOD Thu

**Non-Goals (deferred — not blocking the Friday demo):**
- Custom subdomain `one.yourcompany.com` DNS + cert — runs on existing `platform-frontend` Cloud
  Run URL for the demo. Subdomain is a post-demo polish.
- A separate Cloud Run service for ONE — this deployment **is** ONE. A second service for Aitana's
  own commercial brand becomes relevant when Aitana has a sellable product surface (not this
  sprint).
- Runtime per-user tenant branding — the `?tenant=` URL parameter, `useTenantBranding` hook,
  `TenantBrandingProvider`, and `/api/branding/{domain}` endpoint from earlier drafts are
  **explicitly out**. Branding is per-deployment, baked at build time.
- Separate dev/test/prod for ONE — this is the only ONE deployment this week. Promotion path
  follows the standard v6 dev→test→prod pattern post-demo.
- Telegram / email / WhatsApp channel for ONE — web only. Channels follow standard v6 wiring; not
  on the demo critical path.
- v5 feature parity — the `PPA model/` Python files, `reflect.py`, `references_output.py`,
  `vac_service.py` from `<local-path>` are not ported. The
  demo-relevant subset is the PPA *vocabulary* (prompt) and the BigQuery integration.
- A custom workbench artefact for ONE — the `PPA Cost Estimator` and `PtX Tech Comparator`
  workbenches in M4 of [fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) cover the
  workbench surface; this doc instantiates them with ONE content, doesn't build new ones. The
  `SideBySideDocViewer` is the one genuinely new workbench (M3 centerpiece).
- Per-deployment theme colours / typography — branding override is logo + strings only this sprint.
  Tailwind palette stays default.
- Multi-region / sovereignty constraints — out of scope; ONE is fine with `europe-west1`.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +2 | Branding baked into the bundle at build time — zero runtime resolution, zero network round-trip, zero client flicker. First paint shows ONE branding instantly. ENTSO-E queries pre-compose typed SQL templates; chat path TTFT unaffected. |
| 2 | EARNED TRUST | +2 | ENTSO-E FunctionTool returns `(rows, source_uri="bq://your-entsoe-project.entsoe.day_ahead_prices?...")` so every numeric claim cites its BigQuery table + filter. PPA skill prompt explicitly instructs "cite document blocks for vocabulary definitions, never assert without source". Hits the strongest axiom for a B2B demo. |
| 3 | SKILLS, NOT FEATURES | +2 | `one-ppa-expert` ships as a marketplace skill discoverable in the skill builder. ENTSO-E is an ADK FunctionTool any future skill can reuse. Per-tenant visibility filter is a config field on `ClientConfig`, not new app code. New tenants onboard by editing one Firestore record + adding a skill template — no platform code changes. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | PPA skill uses Gemini 2.5 Flash for vocabulary explanations (fast, deterministic), Claude Sonnet for multi-doc synthesis when the user explicitly invokes "deep analysis". ENTSO-E query path uses zero LLM tokens — typed SQL template + BigQuery, results rendered directly. |
| 5 | GRACEFUL DEGRADATION | +1 | Tenant branding falls back to `BRANDING` defaults if Firestore lookup fails, errors, or the domain is unmapped (existing fallback pattern in `clients.py`). ENTSO-E tool returns a structured error string (not exception) when BigQuery is unreachable, so the agent can recover and explain. Empty document corpus returns empty RAG results — no crash. |
| 6 | PROTOCOL OVER CUSTOM | +2 | Zero new protocols. Branding is build-time env-var config. PPA skills are standard Agent Skills `SKILL.md` (frontmatter + body). ENTSO-E + extract_ppa_clauses + compare_ppa_contracts are standard ADK `FunctionTool`s. Workbench artefacts mount via A2UI surface (v6.2.0 2.9), state-back via surface-context (2.10). Every new surface is existing-protocol. |
| 7 | API FIRST | +1 | Backend exposes only standard `/api/skills` (filtered) + existing v6.3.0 admin routes. No new branding endpoints. PPA + doc-compare skills exercised identically from web chat, CLI (`aiplatform sessions run`), or any future channel. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Existing AG-UI / Cloud Trace coverage continues; every tool span (extract_ppa_clauses, compare_ppa_contracts, entsoe_day_ahead_prices) captured to the same internal sinks. Build-time branding env vars logged at startup so deploy diagnostics are clear. |
| 9 | SECURE BY CONSTRUCTION | +1 | Skill visibility filter enforced server-side — client-side filter would let a curious user enumerate. BigQuery FunctionTool runs as `sa-platform` with table-level grant to `your-entsoe-project.entsoe.*` only — no broader project access. Branding is public bundle code with no PII. All data stays inside GCP edge. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Branding consumption pattern unchanged (existing `BRANDING.*` reads stay as-is) — no new hook, no provider, no client-side resolution. Bundle impact essentially zero. ENTSO-E rendering is via existing A2UI surface; clause/diff cards are A2UI primitives; no new client business logic. |
| | **Net Score** | **+14** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Standards Compliance

This doc adopts established protocols at every protocol boundary:

- **Agent Skills spec** — `one-ppa-expert/SKILL.md` is standard frontmatter + prompt body, loaded
  by `SkillToolset`
- **ADK FunctionTool** — `entsoe_query` is a standard ADK tool with typed signature, docstring,
  and return type
- **A2UI v0.9** — ENTSO-E results render via A2UI surface emit (table + chart components), same
  pattern as the existing `rag_tool.py` document citations
- **AG-UI** — tenant resolution + skill list + ENTSO-E queries all flow through existing event
  streams; no new event types
- **Firestore client-tenant-management API** — branding is an additive field on `ClientConfig`,
  registered via the existing `aiplatform client set` CLI (extended with branding flags)

No custom formats, no proprietary interfaces.

## CLI Surface

Per design-doc-creator skill rule 5b-bis:

- `aiplatform client set <domain> --enabled-skills <comma-separated-slugs>` — extends the existing
  `client set` command from v6.3.0 with the new `--enabled-skills` flag. Branding flags
  (`--logo-url`, `--hero-title`, etc.) are **NOT added** — branding is per-deployment env vars,
  not per-domain runtime config.
- `aiplatform demo verify` — extends the verify command from
  [fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) M5 to assert ONE branding strings
  appear in the rendered HTML on the deployed URL, the ONE-enabled skills appear in the
  marketplace, and an ENTSO-E query returns rows. No `--tenant` flag — the URL itself is the
  ONE deployment.
- `aiplatform tenant probe <domain>` — debug helper: prints the resolved `ClientConfig`, the
  documents bucket contents (top 10 files), the enabled-skills list, and a sample ENTSO-E ping
  against the configured BigQuery datasets. ~30 LOC. Saves "is this tenant set up right" being a
  multi-step gcloud + Firestore-console + curl session.
- `aiplatform docs extract-clauses <doc-id>` — debug helper that runs `extract_ppa_clauses`
  standalone and prints the typed `PpaClauses` output. ~30 LOC.

Each CLI command is ~0.15d. All land in M4.

## Design

### Overview

Three layers, each independently shippable:

1. **Per-deployment branding** — extend `branding.ts` to read `NEXT_PUBLIC_BRAND_*` env vars with
   Sunholo defaults. ONE branding env vars are set in the existing `platform-frontend` Cloud Build
   config + Cloud Run service. Baked into the bundle at build time. Zero runtime overhead.
2. **Per-user (defence-in-depth) skill filter** — `ClientConfig.enabled_skills: list[str] | None`,
   server-side filter on `GET /api/skills`. Existing v6.3.0 mechanism, additive nullable field. ONE
   visitors land on the deployment and get ONE skills via Firestore; admin domains who log in get
   their unfiltered view.
3. **ONE-specific content** — one new chat skill (`one-ppa-expert`), one new workbench skill
   (`one-doc-compare`), three new ADK FunctionTools (`extract_ppa_clauses`,
   `compare_ppa_contracts`, `entsoe_day_ahead_prices`), one new workspace component
   (`SideBySideDocViewer`), one `PpaClauses` Pydantic schema. The demo's visible surface.

### Per-Deployment Branding Flow

```
Cloud Build (this repo) → builds platform-frontend image with NEXT_PUBLIC_BRAND_* baked in
        │
        ▼
Cloud Run deploys image to platform-frontend service (this is "the ONE deployment")
        │
        ▼
Visitor → https://platform-frontend-<hash>.run.app
        │
        ▼
Bundle contains BRANDING = { appName: "Acme Energy", logo: "/images/logo/acmeenergy-logo.svg", ... }
(baked at build time; the env-var read happened during `next build`)
        │
        ▼
First paint: Acme Energy hero + logo + tagline. No /api/branding fetch. No runtime delay.
```

Sunholo defaults stay in `branding.ts`. The public template at `sunholo-data/ai-protocol-platform`
ships without any `NEXT_PUBLIC_BRAND_*` env vars set — visitors see Sunholo. Anyone forking the
template overrides the env vars in their own `cloudbuild.yaml`.

### branding.ts — Env-Var Driven

`frontend/src/lib/branding.ts` already proves the pattern with `CITATION_SCHEME` and
`TRANSPORT_FIELD` (lines 26-36 read `process.env.NEXT_PUBLIC_*` with fallback strings). Extend it
to the `BRANDING` object:

```typescript
export const BRANDING = {
  appName: process.env.NEXT_PUBLIC_BRAND_APP_NAME || "Sunholo",
  tagline: process.env.NEXT_PUBLIC_BRAND_TAGLINE || "AI Protocol Platform",
  description:
    process.env.NEXT_PUBLIC_BRAND_DESCRIPTION ||
    "Open-source AI protocol platform — Skills + AG-UI + A2UI + MCP Apps + A2A on Google ADK",
  logo: {
    favicon: process.env.NEXT_PUBLIC_BRAND_FAVICON || "/images/logo/sunholo-logo.svg",
    heroAnimated: process.env.NEXT_PUBLIC_BRAND_LOGO_HERO || "/images/logo/sunholo-logo.svg",
    chatAvatar: process.env.NEXT_PUBLIC_BRAND_LOGO_AVATAR || "/images/logo/sunholo-logo.svg",
  },
  contact: {
    email: process.env.NEXT_PUBLIC_BRAND_EMAIL || "multivac@sunholo.com",
    githubRepo:
      process.env.NEXT_PUBLIC_BRAND_GITHUB ||
      "https://github.com/sunholo-data/ai-protocol-platform",
  },
} as const;
```

Use `||` not `??` (per G20 / the existing pattern in this file) — Cloud Run injects empty strings
for unset `NEXT_PUBLIC_*` vars, which `??` doesn't catch.

### ONE Branding Env Vars (Cloud Build / Cloud Run)

Set in the existing `cloudbuild.yaml` for `platform-frontend` (the substitution-driven build) +
the Cloud Run service env vars (so Next.js's build picks them up). The exact location depends on
how the frontend image is built; if the build runs inside Cloud Build with `--build-arg`,
substitutions go there. If env vars are set on the Cloud Run service and consumed at build via a
runner step, they go on the service.

```yaml
# cloudbuild.yaml additions
substitutions:
  _BRAND_APP_NAME: "Acme Energy"
  _BRAND_TAGLINE: "PPA & PtX intelligence"
  _BRAND_DESCRIPTION: "Power Purchase Agreement and Power-to-X transaction advisory"
  _BRAND_FAVICON: "/images/logo/acmeenergy-logo.svg"
  _BRAND_LOGO_HERO: "/images/logo/acmeenergy-logo.svg"
  _BRAND_LOGO_AVATAR: "/images/logo/acmeenergy-logo.svg"
  _BRAND_EMAIL: "hello@acme-energy.example"
  _BRAND_GITHUB: ""  # ONE is not open-source
```

Plus a build step that injects these as `NEXT_PUBLIC_BRAND_*` env vars into `next build`.

ONE logo asset gets committed to `frontend/public/images/logo/acmeenergy-logo.svg` (one SVG file;
the repo already has `sunholo-logo.svg` and `aitana-logo.svg` as references).

### Data Model — Extended ClientConfig

`backend/db/clients.py`, single additive field for the per-user skill filter (no branding field —
branding is per-deployment, not per-tenant):

```python
class ClientConfig(BaseModel):
    domain: str
    documents_bucket: str | None = None
    display_name: str = ""

    # NEW in this sprint:
    enabled_skills: list[str] | None = None  # None = all skills visible (existing default)
```

Backwards compatible — every field nullable / defaulted. No migration needed.

### Backend Changes

**Modified endpoint (only — no new endpoints):**

```
GET  /api/skills              -> filter response by user's ClientConfig.enabled_skills
                                 (if user is unauthed or tenant has no enabled_skills, return all)
```

No `/api/branding/*` endpoint. No runtime branding resolution. Backend doesn't know what the
deployment is branded as — it just serves the API.

**New skill:** `backend/skills/templates/one-ppa-expert/SKILL.md`. Port the v5 prompt verbatim into
the body (it is already well-shaped — PPA / PtX / BESS vocabulary, REDII/REDIII rules,
electrolyzer technology comparison, BESS cycle / C-rate definitions). Tools: `list_documents`,
`get_document_content`, `google_search`, `entsoe_query`. Model: `gemini-2.5-flash` for vocabulary
turns; instruction-tier escalation to Claude Sonnet for multi-doc synthesis (existing skill-config
field).

**New ADK FunctionTool:** `backend/tools/entsoe_query.py`. Typed SQL templates over
`your-entsoe-project.entsoe.*` — not free-form SQL generation. Three operations matched to the
demo flow:

```python
def entsoe_day_ahead_prices(
    bidding_zone: str,           # e.g. "DK1", "DE_LU", "FR"
    start_date: str,             # ISO YYYY-MM-DD
    end_date: str,               # ISO YYYY-MM-DD
) -> dict:
    """Returns {"rows": [{ts, price_eur_mwh}], "source_uri": "bq://...?filter=..."}"""
    ...

def entsoe_load(bidding_zone: str, start_date: str, end_date: str) -> dict: ...

def entsoe_generation_mix(
    bidding_zone: str,
    start_date: str,
    end_date: str,
    technology: str | None = None,    # "solar" | "wind_onshore" | etc., None = all
) -> dict: ...
```

All three are read-only, parameterised, return `(rows, source_uri)` so the agent cites BQ. Errors
surface as `{"error": "<msg>", "source_uri": None}` — not exceptions (Axiom #5).

**Modified skill-list endpoint:** `backend/skills/routes.py` filters server-side by
`user.tenant.enabled_skills` if set.

**No new admin/auth surface** — `aitana-admin` group tag from v6.3.0 still gates `client set`.

### Frontend Changes

**`branding.ts` extended** to read `NEXT_PUBLIC_BRAND_*` env vars with Sunholo defaults (see code
block above). ~10 LOC change.

**No hook, no provider, no `BRANDING.` consumer updates.** Every existing import of `BRANDING`
from `lib/branding.ts` keeps working exactly as today — it just resolves to ONE's strings
because the env vars are set at build time. Zero churn on the 8–12 components that read
`BRANDING`. Zero new files. Zero runtime cost.

**One new SVG asset** — `frontend/public/images/logo/acmeenergy-logo.svg`. Sunholo and Aitana
logo files already live in the same directory as references.

### Demo Surfaces — Skills, A2UI, MCP Apps, Workbenches

The demo story's centerpiece is **"compare this PPA contract to this PPA contract"** — a high-value
ONE consulting task that exercises parsed-document blocks, A2UI surface rendering, agent reasoning
over structured content, and (optionally) ENTSO-E-grounded valuation of the differences.

The visible surfaces in the demo:

**1. Skills (3 enabled for `acme-energy.example` via `ClientConfig.enabled_skills`):**

| Skill slug | Role in demo | Primary surface |
|---|---|---|
| `one-ppa-expert` | Chat skill — PPA / PtX / BESS vocabulary, RAG over ONE corpus, ENTSO-E FunctionTool | Chat + inline A2UI cards |
| `one-doc-compare` | **Workbench centerpiece** — side-by-side comparison of two PPA contracts with agent-driven "key differences" reasoning | Workspace surface + chat |
| `general-assistant` | Fallback for off-topic questions; keeps the marketplace from feeling empty | Chat |

**2. A2UI components (declarative cards rendered inline in chat OR in the workbench surface):**

These are the "nice A2UI" the user expects within reach — all built from existing A2UI v0.9
primitives (Container / Heading / List / Text / DataTable + chart components from the artefact
render hook v6.2.0 2.13). No new A2UI primitives invented.

| Component | Emitted by | Renders | Notes |
|---|---|---|---|
| **PPA Contract Summary Card** | `one-ppa-expert` after RAG retrieval | Structured fields: counterparty / volume / term / settlement type / price formula / RtM | Pure A2UI; one card per cited contract |
| **Clause Extraction Card** | `extract_ppa_clauses` FunctionTool | Structured clause-by-clause panel: each row = clause name + extracted value + `aitana://doc/{docId}/block/{blockId}` citation chip + confidence badge | **Centerpiece of EARNED TRUST.** One clause card per contract, expand-to-see-source-block on click. Drives the doc-compare narrative. |
| **Key Differences Summary** | `one-doc-compare` | Ordered list of clause-level diffs with severity badges + click-to-explain; each diff cites BOTH source block_ids | Pure A2UI; renders into workbench. Driven by the same `PpaClauses` schema the extraction card uses |
| **ENTSO-E Price Chart Card** | `entsoe_day_ahead_prices` FunctionTool | Line chart + table + `source_uri` citation chip | Uses A2UI chart component from artefact render hook (v6.2.0 2.13) |
| **Citation Chip** | every tool that returns sources | Compact chip with `aitana://doc/{docId}/block/{blockId}` or `bq://...` link | Existing; reused (Axiom 2) |
| **PPA Cost Estimator inputs/output** | `compute_ppa_cost` (from 4.1 M4) | Form + result panel; ENTSO-E-grounded baseline | Existing 4.1 component, ONE config |

### Structured Clause Extraction — `PpaClauses` schema

A single shared Pydantic schema drives the Clause Extraction Card AND the Key Differences Summary
AND the compare tool's diff output. Define once, reuse three ways (Axiom 6: protocol over custom).

```python
# backend/tools/schemas/ppa_clauses.py

class ClauseExtraction(BaseModel):
    """One extracted clause with provenance."""
    clause_name: str                    # e.g. "settlement_type", "price_formula", "term_length"
    display_name: str                   # human-readable: "Settlement Type"
    value: str | None                   # normalized extracted value (e.g. "PaP", "Fixed €45/MWh, CPI-indexed")
    raw_excerpt: str                    # exact contract text the value came from
    block_id: str                       # aitana://doc/{docId}/block/{block_id} citation target
    confidence: Literal["high", "medium", "low"]
    notes: str | None = None            # extractor's caveats, e.g. "definition references Annex A"


class PpaClauses(BaseModel):
    """Standard structured output for a single PPA contract."""
    doc_id: str
    counterparty_buyer: ClauseExtraction | None
    counterparty_seller: ClauseExtraction | None
    volume_mwh: ClauseExtraction | None
    term_years: ClauseExtraction | None
    settlement_type: ClauseExtraction | None        # PaP / PaN / BL
    contract_form: ClauseExtraction | None          # Physical / Financial-FS / Financial-PS
    price_formula: ClauseExtraction | None
    rtm_provider: ClauseExtraction | None           # who provides route-to-market
    force_majeure: ClauseExtraction | None
    change_of_law: ClauseExtraction | None
    termination: ClauseExtraction | None
    governing_law: ClauseExtraction | None
    other_clauses: list[ClauseExtraction] = []      # catch-all for contract-specific clauses


class PpaComparison(BaseModel):
    """Output of compare_ppa_contracts — diff over PpaClauses."""
    left: PpaClauses
    right: PpaClauses
    differences: list[ClauseDifference]


class ClauseDifference(BaseModel):
    clause_name: str
    severity: Literal["material", "moderate", "cosmetic"]
    left_value: str | None
    right_value: str | None
    left_block_id: str | None
    right_block_id: str | None
    commercial_implication: str         # 1-2 sentence agent-generated explanation
```

**Why a typed schema and not free-form prose:**
- **Axiom 2 EARNED TRUST** — every extracted value carries a `block_id` citation, every diff
  carries both source block_ids, every confidence level is explicit
- **Axiom 6 PROTOCOL OVER CUSTOM** — the same schema renders three different A2UI surfaces; the
  same tool composes with `entsoe_day_ahead_prices` for the price-valuation step
- **Axiom 4 RIGHT MODEL** — extraction is structured-output mode (Pydantic schema via ADK's
  `output_schema`), uses Claude Sonnet once per document then the cards render deterministically.
  No re-prompting for the rendering.
- **Reusable beyond ONE** — any contract-review fork (legal, procurement) can build on the same
  `ClauseExtraction` primitive with a different schema (e.g. `MsaClauses`, `EmploymentClauses`).

**3. MCP App (placeholder for the demo — sandboxed iframe):**

| App | What it does | Demo-grade scope |
|---|---|---|
| **PtX Tech Comparator** (wraps 4.1's `VendorComparisonValidator`) | SOEC / PEM / Alkaline table with hardcoded CapEx / OpEx / efficiency / lifetime / CO₂ rows from the v5 prompt's tech catalog | Static iframe — demonstrates the MCP App sandbox + Audit pane integration without needing a real validator. Click "Compare SOEC vs PEM" → 4.1 M3 specialist chip lights up. |

The user explicitly accepted this as a placeholder (2026-06-08). Replacing it with a real
data-driven validator is post-demo work (tracked in Open Q4).

**4. Workbench surfaces (mounted via A2UI multi-surface API, v6.2.0 2.9):**

| Workbench | Skill | Component | Status |
|---|---|---|---|
| **Single-doc viewer** | any skill that emits doc references | `ParsedDocViewer` (existing, reads AILANG-parse blocks) | Existing — no work this sprint |
| **Side-by-side doc compare** | `one-doc-compare` | `SideBySideDocViewer` — new component | **NEW this sprint (M3 centerpiece)** |
| **PPA Cost Estimator** | `one-ppa-expert` | `RoiCalculatorWorkbench` repurpose from 4.1 M4 | ONE config only |
| **PtX Tech Comparator** | `one-ppa-expert` | `VendorComparisonValidator` repurpose from 4.1 M4 | ONE config only |

### Side-by-side doc compare — the M3 centerpiece

The `one-doc-compare` skill is the demo's strongest narrative beat. The workflow:

1. **User uploads or picks** two PPA contracts from `gs://multivac-acme-energy-bucket/PPA/` (multi-select in
   the marketplace doc picker, or drag-drop two files).
2. **Workbench opens** in compare mode: two columns, each rendering an AILANG-parsed contract
   via the existing block model. Synchronized scroll. Diff highlights at the block level (added /
   removed / modified spans).
3. **Agent runs a structured comparison** via `compare_ppa_contracts(left_doc_id, right_doc_id)`
   FunctionTool. Output is a key-differences A2UI card mounted to the workbench header:
   - Defined terms diff (e.g., "Force Majeure" definition expanded in B)
   - Settlement type diff (e.g., PaP vs PaN)
   - Price formula diff (e.g., fixed vs CPI-indexed)
   - Term length diff
   - Termination clauses diff
4. **User clicks a difference row** → A2UI surface-context (v6.2.0 2.10) pushes the selected diff
   context to the agent's next turn. The agent explains the commercial implication in chat,
   citing both source blocks.
5. **(Optional, time-permitting)** Agent uses `entsoe_day_ahead_prices` to estimate the cash
   impact of the price-formula difference at recent DK1 prices: "Over the past 30 days at DK1,
   contract A's fixed price would have paid €X/MWh; contract B's CPI-indexed would have paid
   €Y/MWh. Across the 50 MWh volume, that's a €(X-Y)*50/MWh delta."

This is the 5-minute demo narrative.

**Component scope for `SideBySideDocViewer`:**
- Split-pane layout reusing `WorkspaceShell` + `WorkspaceDivider` from 4.1 M1
- Each pane renders an existing single-doc viewer instance (reusing `ParsedDocViewer`)
- Block-level diff highlight overlay — algorithm: align by AILANG block ID (when present), then
  fall back to text-similarity matching. ~150 LOC for the diff algorithm + ~250 LOC for the
  rendering layer + sync scroll.
- Click handler on any diff span → `surface-action` event with the diff descriptor
- ~400 LOC total + ~5 Vitest tests

**Tool scope for `compare_ppa_contracts`:**
- ADK FunctionTool: `compare_ppa_contracts(left_doc_id: str, right_doc_id: str) -> dict`
- Loads both documents from the user's tenant bucket via existing `get_document_content`
- Extracts AILANG block structure
- Runs a structured-comparison prompt (Claude Sonnet — Axiom 4 reasoning model for the complex
  multi-section diff) returning typed `{differences: [{section, severity, left_excerpt,
  right_excerpt, left_block_id, right_block_id, summary}]}`
- ~200 LOC + ~5 tests

**Skill scope for `one-doc-compare/SKILL.md`:**
- Frontmatter: tools = `[list_documents, get_document_content, compare_ppa_contracts,
  entsoe_day_ahead_prices]`, model = `gemini-2.5-flash` for orchestration / `claude-sonnet-4` for
  comparison synthesis (existing skill-config model-tier escalation)
- Prompt body: PPA vocabulary subset (inherits from `one-ppa-expert`'s prompt via include or
  duplication for the sprint), comparison rubric ("when comparing two PPAs, examine: definitions,
  settlement, price, term, termination, change-of-law, force majeure, indemnity")
- ~120 LOC including prompt body

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET    | /api/skills | Response filtered by `ClientConfig.enabled_skills` when authed | Yes — additive filter, behaviour change for tenants with `enabled_skills` set. Backwards compat: null = all skills (existing v6.3.0 behaviour). |
| PUT    | /api/admin/clients/{domain} | Accepts new optional `enabled_skills` field | No (additive nullable) |
| GET    | /api/admin/clients/{domain} | Response gains optional `enabled_skills` field | No (additive nullable) |

No `/api/branding/*` endpoints. Branding is build-time on the frontend.

### Architecture Diagram

```
BUILD TIME (Cloud Build):
   cloudbuild.yaml substitutions
        |
        +-- _BRAND_APP_NAME=Acme Energy
        +-- _BRAND_LOGO_HERO=/images/logo/acmeenergy-logo.svg
        +-- _BRAND_TAGLINE=PPA & PtX intelligence
        +-- ...
        |
        v
   docker build (Next.js next build with NEXT_PUBLIC_BRAND_* env)
        |
        v
   europe-west1-docker.pkg.dev/.../platform-frontend/ui:dev
        |
        v
   gcloud run deploy platform-frontend ... (this Cloud Run service)


RUNTIME (any visitor, authed or not):
   Visitor -> https://platform-frontend-<hash>.run.app
        |
        v
   Bundle's BRANDING = { appName: "Acme Energy", ... } (baked in)
        |
        v
   First paint: ONE hero + logo + tagline. Zero network round-trip for branding.
        |
        v
   [@acme-energy.example signs in] -> ID token -> backend resolves
                                                  |
                                                  +-- documents_bucket = multivac-acme-energy-bucket (v6.3.0)
                                                  +-- enabled_skills = [one-ppa-expert,
                                                       one-doc-compare, general-assistant]
        |
        v
   Marketplace renders 3 ONE skills
        |
        v
   [Semantic search "find PPAs with RFNBO compliance clauses"] -> one-ppa-expert
                |
                v
   ai_search via VertexAiSearchTool
                |
                v
   Vertex Search datastore "one_generic" in multivac-acme-energy
                                              |
                                              v
   indexed gs://multivac-acme-energy-bucket/ -> cited chunks back to A2UI card
        |
        v
   [Click one-doc-compare, pick 2 PPA contracts] -> /chat/one-doc-compare
        |
        v
   WorkspaceShell mounts SideBySideDocViewer (M3 new)
                                   |
                                   +-- left pane: ParsedDocViewer (contract A)
                                   +-- right pane: ParsedDocViewer (contract B)
                                   +-- block-level diff overlay
                                   +-- KeyDifferencesPanel (A2UI artefact, top)
                                              |
                                              v
                      compare_ppa_contracts(left_doc_id, right_doc_id) -> PpaComparison
                                              |
                                              v
                       extract_ppa_clauses(A) || extract_ppa_clauses(B) (parallel)
                                              |
                                              v
                       Claude Sonnet structured comparison -> {differences: [...]}
        |
        v
   [User clicks "Price formula" diff] -> surface-action -> agent next turn
                                                              |
                                                              v
                       "Cost at DK1 prices?" -> entsoe_day_ahead_prices(DK1, ...)
                                                              |
                                                              v
                       BigQuery: your-entsoe-project.entsoe.day_ahead_prices
                                                              |
                                                              v
                       {rows, source_uri="bq://..."} -> A2UI chart card with citation chip
```

## Implementation Plan

Day-by-day for Tue 2026-06-09 -> Fri 2026-06-12. Each milestone independently revert-safe.

### M1 — Per-Deployment Branding (Tue, ~0.3d)

Massively shrunk after the per-deployment refactor. Maybe even Mon evening.

- [ ] Frontend: extend `frontend/src/lib/branding.ts` `BRANDING` object to read `NEXT_PUBLIC_BRAND_*` env vars with current Sunholo values as fallbacks (~10 LOC + 2 tests covering fallback + override)
- [ ] Asset: commit `frontend/public/images/logo/acmeenergy-logo.svg` (single SVG, sourced from v5 ONE assets or new design — confirm with ONE which logo to use; placeholder OK for Tue)
- [ ] Cloud Build: add `_BRAND_*` substitutions + Docker `--build-arg NEXT_PUBLIC_BRAND_*` flow to the existing `platform-frontend` build config. Reference: how `NEXT_PUBLIC_CITATION_SCHEME` is currently injected. (~15 LOC in `cloudbuild.yaml`)
- [ ] Deploy: trigger a build, verify ONE branding appears on the live `platform-frontend-...run.app` URL via chrome-devtools MCP
- [ ] Backend: extend `ClientConfig` with `enabled_skills: list[str] | None` (single field, no `BrandingConfig`) (~10 LOC + 3 tests)
- [ ] Backend: extend `/api/skills` route to filter by `user.tenant.enabled_skills` server-side (~20 LOC + 3 tests covering enabled / null / not-in-list)
- [ ] Backend: extend `aiplatform client set` CLI with `--enabled-skills` flag only (no branding flags) (~10 LOC + 1 test)
- [ ] Manual: `aiplatform client set acme-energy.example --enabled-skills one-ppa-expert,one-doc-compare,general-assistant` — verify the marketplace shows only those 3 to ONE users

### M1 Buffer
M1 finishing in ~0.3d frees ~0.7d. Roll those hours into M3 (doc-compare workbench, the
demo centerpiece) where the diff-alignment quality risk (Open Q7) lives.

### M2 — ONE Skill + Real Corpus Wire-Up + Clause Extraction + ENTSO-E (Wed, ~1d)

This day combines the chat skill, **pointing v6 at ONE's existing corpus bucket**, the
clause-extraction tool, AND a minimal ENTSO-E tool. The corpus already exists with years of real
data — no upload step.

- [ ] **Skill** — `backend/skills/templates/one-ppa-expert/SKILL.md` — port v5 `prompt.txt` body, frontmatter sets `tags: [ppa, energy, acme-energy.example]`, `model: gemini-2.5-flash`, `tools: [ai_search, list_documents, get_document_content, extract_ppa_clauses, entsoe_day_ahead_prices, google_search]`, `toolConfigs.ai_search.datastore: <ONE-vertex-search-datastore-id>` (likely `one_generic` per v5 vac_config — exact resource path needs confirming via the GCP Console for `multivac-acme-energy`). The `ai_search` path is the semantic-RAG entry; `list_documents`/`get_document_content` is the direct-GCS path for known docs. (~120 LOC including prompt body + 2 tests via `load_skill_from_dir`)
- [ ] **Skill filter** — extend `/api/skills` endpoint to filter by `tenant.enabled_skills` server-side (~20 LOC + 3 tests covering enabled / null / not-in-list paths)
- [ ] **Skill publish** — register skill in Firestore via existing `aiplatform skill push` flow
- [ ] **Corpus IAM (cross-project, two grants)** — on the `multivac-acme-energy` project, grant `sa-platform@your-project-id.iam.gserviceaccount.com` two roles: `roles/storage.objectViewer` on `gs://multivac-acme-energy-bucket` (for direct GCS reads by `list_documents`/`get_document_content`/`extract_ppa_clauses`) AND `roles/discoveryengine.viewer` (or finer `discoveryengine.searchEditor` if needed for query) at project level for the Vertex Search datastore. ~10 min total. Track terraform follow-up per `feedback_no_manual_iam_grants`.
- [ ] **Find the exact Vertex Search datastore ID** — v5 `vac_config.yaml` references `one_generic` but the canonical Vertex AI Search resource path looks like `projects/multivac-acme-energy/locations/<global|eu>/collections/default_collection/dataStores/<id>`. Confirm via the GCP Console for `multivac-acme-energy` → AI Applications → Data Stores. Update the SKILL.md frontmatter.
- [ ] **Tenant config** — `aiplatform client set acme-energy.example --documents-bucket multivac-acme-energy-bucket --enabled-skills one-ppa-expert,one-doc-compare,general-assistant` — wires v6 to ONE's real corpus.
- [ ] **Corpus scoping** — verify the agent's `list_documents` + `get_document_content` work against the real bucket. Two scoping options for the demo: (a) trust agent to query `PPAs/longform/` and `PPAs/termsheets/` by path filter in the skill prompt; (b) add a `documents_path_filter` field to `ClientConfig` (~10 LOC) if (a) is too noisy. Decide during M2 PM smoke-test.
- [ ] **Pick demo contracts** — choose 2 specific PPA contracts from `PPAs/longform/` for the M3 doc-compare demo by Wed EOD. Ideally same-template-derived for clean `block_id` alignment, with at least 3 material clause differences (settlement type, price formula, term). Confirm with ONE that the chosen pair is OK for the demo.
- [ ] **Clause schema + tool** — `backend/tools/schemas/ppa_clauses.py` defines `ClauseExtraction` + `PpaClauses` + `PpaComparison` + `ClauseDifference` Pydantic models (~120 LOC). `backend/tools/extract_ppa_clauses.py` ADK FunctionTool: loads doc via `get_document_content`, runs structured-output extraction (ADK `output_schema=PpaClauses`, model = Claude Sonnet), returns typed `PpaClauses` with `block_id` for every clause (~200 LOC + 5 tests).
- [ ] **A2UI Clause Card** — frontend `ClauseExtractionCard.tsx` component rendering `PpaClauses` as a table with clause name | extracted value | confidence badge | citation chip. Mounted via artefact render hook (v6.2.0 2.13) — no new render plumbing (~180 LOC + 3 Vitest tests)
- [ ] **ENTSO-E (compressed)** — `backend/tools/entsoe_query.py` with ONE function `entsoe_day_ahead_prices(bidding_zone, start_date, end_date)`. Other two functions (`entsoe_load`, `entsoe_generation_mix`) deferred to Open Q6 — not on the demo critical path. (~80 LOC + 3 tests + 1 integration test gated on env var)
- [ ] **BQ IAM (cross-project)** — grant `sa-platform@your-project-id.iam.gserviceaccount.com` role `roles/bigquery.dataViewer` on `your-entsoe-project.entsoe` dataset (cross-project from `your-project-id` to `your-entsoe-project`; one gcloud command; ~5 min)
- [ ] **Smoke** — PPA skill answers "find contracts mentioning RFNBO compliance" via `ai_search` (semantic over ONE's Vertex Search index) with cited extracts; the same skill answers "summarise [picked-contract-name]" via `list_documents` + `get_document_content` + AILANG-parse; `extract_ppa_clauses` runs against one of the picked demo contracts and returns typed `PpaClauses`; ENTSO-E returns last 7d DK1 prices.

### M3 — Doc Compare Workbench (Thu, ~1d) — DEMO CENTERPIECE

The "compare this PPA contract to this PPA contract" surface. Reuses the clause schema from M2.

- [ ] **Skill** — `backend/skills/templates/one-doc-compare/SKILL.md` — orchestrator skill, frontmatter `tools: [list_documents, extract_ppa_clauses, compare_ppa_contracts, entsoe_day_ahead_prices]`, `model: gemini-2.5-flash` for orchestration / `claude-sonnet-4` for comparison synthesis. Prompt body: comparison rubric covering definitions / settlement / price / term / termination / change-of-law / force-majeure / indemnity (~120 LOC including prompt body + 2 tests)
- [ ] **Compare tool** — `backend/tools/compare_ppa_contracts.py` ADK FunctionTool: takes `(left_doc_id, right_doc_id)`, calls `extract_ppa_clauses` twice (parallelised), runs structured comparison (Claude Sonnet, `output_schema=PpaComparison`), returns typed `PpaComparison` with block_id citations on both sides. Each `ClauseDifference` includes a `commercial_implication` field — short agent-generated explanation. (~200 LOC + 5 tests with mocked extract output)
- [ ] **`SideBySideDocViewer` component** — `frontend/src/components/workspace/SideBySideDocViewer.tsx`. Composition:
  - Outer `WorkspaceShell` (from 4.1 M1) with two equal panes
  - Each pane wraps existing `ParsedDocViewer` (single-doc viewer already in upstream — read AILANG blocks)
  - Block-level diff highlight overlay: align by `block_id` when present, fall back to text-similarity matching for unmatched blocks (~150 LOC for align algorithm)
  - Synchronized scroll between panes (`useSyncedScroll` hook; ~50 LOC)
  - Click handler on any highlighted span emits `surface-action` with the diff descriptor → next agent turn explains the diff via surface-context (v6.2.0 2.10)
  - ~400 LOC total + 5 Vitest tests
- [ ] **A2UI Key Differences card** — `KeyDifferencesPanel.tsx` mounted at the top of the workbench. Renders `PpaComparison.differences` as an ordered list with severity badges (material / moderate / cosmetic), expand-row reveals `left_excerpt` + `right_excerpt` + `commercial_implication`. Click → `surface-action`. (~150 LOC + 3 tests)
- [ ] **Workspace mount** — `one-doc-compare` skill's agent emits an A2UI artefact targeting the `workspace` named surface (v6.2.0 2.9) at the start of every turn that has two doc IDs in context. Surface persists across turns.
- [ ] **PPA Cost Estimator (4.1 M4 repurpose)** — configure `RoiCalculatorWorkbench` for the `one-ppa-expert` skill with PPA-specific inputs (volume MWh / term years / settlement type / DK1 baseline from ENTSO-E). Light configuration, no new component code (~30 LOC backend skill-config + ~20 LOC `compute_ppa_cost` function wrapping `entsoe_day_ahead_prices`)
- [ ] **PtX Tech Comparator (4.1 M4 repurpose)** — `VendorComparisonValidator` instantiated with hardcoded SOEC / PEM / Alkaline rows from v5 prompt's tech catalog. Placeholder per Open Q4; no real validator agent. (~50 LOC config)
- [ ] **Manual** — upload 2 fixture PPA contracts, open `one-doc-compare` skill, verify workbench mounts with side-by-side view + key differences panel; click a price-formula diff → agent explains in chat with both `block_id` citations

### M4 — Demo Wiring + CLI + Rehearsal (Fri, ~0.5d + rehearsal buffer)
- [ ] **CLI** — `aiplatform tenant probe acme-energy.example` prints resolved ClientConfig, top 10 bucket files, enabled-skills, sample ENTSO-E ping, sample clause-extraction on a fixture doc (~40 LOC + 1 test)
- [ ] **CLI** — extend `aiplatform demo verify` (from 4.1 M5) with `--tenant <domain>` flag (~20 LOC + 1 test)
- [ ] **CLI** — `aiplatform docs extract-clauses <doc-id>` debug helper — runs `extract_ppa_clauses` standalone, prints typed output. Saves "is extraction working?" being a multi-step backend call. (~30 LOC + 1 test)
- [ ] **Smoke** — add ONE-tenant assertions to `scripts/smoke-deployed.sh dev all` — assert `/?tenant=acme-energy.example` returns ONE branding strings (~15 LOC + run once green)
- [ ] **Rehearsal** — run the 5-minute demo flow end-to-end with chrome-devtools MCP (use the `aitana-frontend-verify` skill). Capture screenshots. Log UX issues.
- [ ] **Fix-it buffer** — ~2h reserved for rehearsal-surfaced issues
- [ ] Update `docs/talks/ai-ui-protocol-stack.md` verification log entry — "v6.4.0 / 2026-06-12: multi-tenant ONE demo — branding + clause-extraction + side-by-side compare + ENTSO-E grounding all confirmed"
- [ ] `make demo-verify-one` Makefile target wrapping `aiplatform demo verify --tenant acme-energy.example`

### Parallel-track risk

M1 (branding) and 4.1 M2 (landing components / Hero / ProtocolStripe) edit overlapping files. If
4.1 M2 hasn't shipped by Tue EOD, M1 ports the components inline (the v6.4.0 design doc already
specifies them as straightforward ports from gde-ap-agent — low-merge-risk to do them inside this
sprint and let 4.1 rebase).

**Mitigation:** M1 first task is to confirm 4.1 M2 status. If shipped, build on top. If not, port
the components ourselves and notify the 4.1 sprint owner.

## Migration & Rollout

**Database Migrations:**
- None. `ClientConfig.enabled_skills` is a nullable additive field. Existing ONE Firestore record
  (created by v6.3.0 onboarding recipe) gets the new field via the extended `aiplatform client set`
  CLI.

**Feature Flags:**
- Tenant skill filter activates only when `enabled_skills` is non-null. Setting it to null reverts
  to "all skills visible" behaviour. Per-domain flip.

**Rollback Plan:**
- M1 — branding rollback: unset `NEXT_PUBLIC_BRAND_*` substitutions in `cloudbuild.yaml`,
  redeploy. Sunholo defaults take over. ~5 min.
- M1 — skill filter rollback: set `clients/acme-energy.example.enabled_skills` to null. All skills
  visible. No code change.
- M2: delete skill template directories + Firestore records; tenant skill filter falls back to "all
  skills" if `enabled_skills` is unset.
- M3: revert FunctionTool registrations in SKILL.md; tools become unreachable from agents but don't
  break the rest. BigQuery IAM grant can be revoked independently.
- M4: CLI commands are additive; can be removed without affecting runtime.
- Each milestone independently revert-safe; nothing depends on a later milestone for safety.

**Environment Variables (NEW — set on `platform-frontend` build):**

| Var | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_BRAND_APP_NAME` | `Acme Energy` | |
| `NEXT_PUBLIC_BRAND_TAGLINE` | `PPA & PtX intelligence` | |
| `NEXT_PUBLIC_BRAND_DESCRIPTION` | `Power Purchase Agreement and Power-to-X transaction advisory` | |
| `NEXT_PUBLIC_BRAND_FAVICON` | `/images/logo/acmeenergy-logo.svg` | |
| `NEXT_PUBLIC_BRAND_LOGO_HERO` | `/images/logo/acmeenergy-logo.svg` | |
| `NEXT_PUBLIC_BRAND_LOGO_AVATAR` | `/images/logo/acmeenergy-logo.svg` | |
| `NEXT_PUBLIC_BRAND_EMAIL` | `hello@acme-energy.example` | |
| `NEXT_PUBLIC_BRAND_GITHUB` | `` (empty) | ONE is not open-source; empty string hides the github link |
| `NEXT_PUBLIC_CITATION_SCHEME` | `one` *(optional)* | rebrands `aitana://doc/...` citation URIs to `one://doc/...` |
| `NEXT_PUBLIC_APP_SLUG` | `one` *(optional)* | rebrands the MCP App transport field |

None unset (defaults to Sunholo). The two optional rebrands are safe to defer; the eight branding
vars are the minimum demo set.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `test_clients_enabled_skills.py` — `enabled_skills` round-trips through Firestore, filter applied on skill list endpoint, null = all skills behaviour (~5 tests)
- [ ] `test_one_ppa_expert.py` — skill loads via `load_skill_from_dir`, validates against `SkillConfig`, runs one happy-path message through ADK Runner with mocked tools (~3 tests)
- [ ] `test_one_doc_compare.py` — same shape (~3 tests)
- [ ] `test_extract_ppa_clauses.py` — happy path on a fixture doc, missing-blocks path, structured output matches `PpaClauses` schema (~5 tests)
- [ ] `test_compare_ppa_contracts.py` — diff over two PpaClauses returns typed `PpaComparison` with `commercial_implication` on every diff (~5 tests with mocked extract output)
- [ ] `test_entsoe_query.py` — `entsoe_day_ahead_prices` BQ-mocked: happy path, error path, source_uri format assertion (~3 tests + 1 integration test gated on `ENTSOE_INTEGRATION_TEST=1`)
- [ ] `test_skill_list_tenant_filter.py` — admin user sees all skills; ONE-domain user sees only `enabled_skills`; unmapped domain sees all (~3 tests)

### Frontend Tests (Vitest + React Testing Library)
- [ ] `branding.ts` — Sunholo defaults when env vars unset; ONE values when set (~3 tests using `process.env` mocking)
- [ ] `ClauseExtractionCard` — renders `PpaClauses` with citation chips + confidence badges (~3 tests)
- [ ] `KeyDifferencesPanel` — renders `PpaComparison.differences` with severity badges; click emits `surface-action` (~3 tests)
- [ ] `SideBySideDocViewer` — split-pane mount, sync scroll, diff overlay alignment, click handler (~5 tests)
- [ ] Marketplace strip respects filtered skill list (~2 tests)

### CLI Tests (pytest)
- [ ] `aiplatform client set --enabled-skills ...` calls PUT with correct payload (~2 tests)
- [ ] `aiplatform tenant probe <domain>` happy path + 404 (~2 tests)
- [ ] `aiplatform demo verify` asserts ONE branding strings in rendered HTML (~2 tests)
- [ ] `aiplatform docs extract-clauses <doc-id>` prints typed output (~2 tests)

### Manual Testing (verified via `aiplatform demo verify` + chrome-devtools MCP — use `aitana-frontend-verify` skill)
- [ ] Visit `platform-frontend-...run.app` unauthenticated → ONE branding renders (logo + hero + tagline). No "Sunholo", no "Aitana" visible.
- [ ] Sign in as `@acme-energy.example` test user → marketplace shows only the 3 ONE-enabled skills
- [ ] Sign in as `@yourcompany.com` admin → marketplace shows full skill catalogue (Mark sees everything)
- [ ] Open `one-ppa-expert` skill → ask "what's the difference between PaP and PaN settlement?" → response uses correct vocabulary with cited document blocks
- [ ] Upload a PPA contract → ask `one-ppa-expert` to "extract clauses" → ClauseExtractionCard renders inline with structured clauses + `aitana://doc/.../block/...` citations
- [ ] Open `one-doc-compare` skill → select 2 PPA contracts → `SideBySideDocViewer` mounts in workbench; KeyDifferencesPanel populates; click a price-formula diff → agent explains in chat with both source `block_id` citations
- [ ] Ask "average DK1 day-ahead price between June 1 and June 7" → ENTSO-E tool fires, returns chart + cited `bq://...` source_uri
- [ ] In doc-compare, ask "what would the price difference cost at DK1?" → composes `compare_ppa_contracts` output with `entsoe_day_ahead_prices` → cited valuation
- [ ] PPA Cost Estimator workbench: enter 50 MWh / 10 years / PaP / DK1 → ROI surface renders with ENTSO-E-grounded baseline price
- [ ] PtX Tech Comparator (placeholder MCP App): click "Compare SOEC vs PEM" → sub-agent specialist chip lights in Audit pane (4.1 M3); InspectorPanel shows the comparison reasoning with citations
- [ ] `aiplatform demo verify` → green checklist asserting ONE branding + skills + tools all work

## Security Considerations

- **No new data flows outside GCP edge.** All telemetry stays in Cloud Trace / Cloud Logging /
  BigQuery (Axiom #8). ENTSO-E queries hit BigQuery in the same trust zone (`your-entsoe-project`
  is another GCP project under Mark's org, not external SaaS — explicit cross-project access via SA
  grant is the standard pattern).
- **Skill visibility enforced server-side.** `enabled_skills` filter happens in `/api/skills`
  before the response leaves the backend. A user editing browser code cannot enumerate hidden
  skills.
- **Branding endpoint is public read.** Tenant branding is not sensitive (logo URL + product
  strings). No auth required, which avoids a chicken-and-egg with the sign-in screen needing to
  fetch its own branding.
- **`?tenant=<domain>` bypass** is dev-only for the demo. Pre-prod, gate to admin group tag —
  otherwise a curious visitor could enumerate every tenant's branded landing. Tracked as Open Q3.
- **BigQuery FunctionTool least-privilege.** `sa-platform` gets `bigquery.dataViewer` on the
  specific datasets used (`entsoe`, `deal_tracker`), not project-wide. Single-direction read.
  Tool's SQL is parameterised templates, not free-form generation — no injection surface.
- **Document corpus tenant isolation.** Already enforced by `resolve_documents_bucket(user)` in
  `clients.py` — ONE users hit `multivac-acme-energy-bucket`; cross-tenant document access is structurally
  impossible. This sprint doesn't touch that path; reaffirms it.
- **PII boundary.** PPA documents may contain commercial counterparty names. They stay in ONE's
  bucket and are surfaced only to ONE users via tenant-scoped RAG. Same boundary as v5.

## Performance Considerations

- **Branding resolution overhead.** One Firestore read on first server render per process per
  domain, cached in-memory for 60s. <30ms first-hit, <1ms cache-hit. Doesn't affect chat path.
- **Skill list filter.** O(n) over the skill catalog (~20 skills today). Negligible.
- **ENTSO-E query latency.** BigQuery cold query ~1–3s for a 7-day range with `LIMIT 200`. Demo
  acceptable. If lower-latency wanted later, materialise to a per-zone hot table or cache the
  last-N-days extract — out of scope for this sprint.
- **Bundle size impact.** `useTenantBranding` hook + provider = ~2KB gzipped. Within
  <200KB budget (Axiom #10).
- **Document corpus indexing.** RAG against `multivac-acme-energy-bucket/PPA/` uses existing AI-Search
  pipeline; index build time on 5–10 docs is <5 minutes. Run once via `aiplatform docs upload`
  during M2.

## Success Criteria

- [ ] All backend tests passing (`cd backend && make lint && make test-fast`)
- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] CLI tests passing (`cd cli && uv run pytest`)
- [ ] `aiplatform demo verify` returns green (asserts ONE branding strings in the live URL)
- [ ] `make smoke-deployed dev all` stays green
- [ ] Deployed `platform-frontend-...run.app` URL renders "Acme Energy" branding (logo + hero
      + tagline) for any visitor, authed or not, without any console errors
- [ ] Zero "Sunholo" / "Aitana" strings visible to a visitor on the deployed URL
- [ ] `@acme-energy.example` Firebase user sees the filtered marketplace (3 ONE-enabled skills)
- [ ] `@yourcompany.com` admin sees full skill catalogue
- [ ] `one-ppa-expert` skill answers a PPA vocabulary question with cited extracts from
      `gs://multivac-acme-energy-bucket/PPA/`
- [ ] `extract_ppa_clauses(doc_id)` returns typed `PpaClauses` with `block_id` citations on every clause
- [ ] `one-doc-compare` skill renders `SideBySideDocViewer` with KeyDifferencesPanel showing
      `commercial_implication` on each diff; click-to-explain flow works
- [ ] `entsoe_day_ahead_prices("DK1", ...)` returns a chart + source_uri citation to the BQ table
- [ ] End-to-end valuation flow works: doc-compare price-formula diff → ENTSO-E-grounded cost estimate
- [ ] PPA Cost Estimator workbench mounts and computes with ENTSO-E-grounded baseline
- [ ] Demo rehearsal walkthrough completes the 5-minute flow on Thursday EOD with no engineering
      intervention
- [ ] Zero hardcoded "Aitana" / "Sunholo" strings outside `branding.ts` defaults (CI grep assertion
      from 4.1) — proves every visible string flows through the env-var-driven branding
- [ ] `docs/talks/ai-ui-protocol-stack.md` verification log entry added

## Open Questions

- **Q1 — Demo URL.** Two options:
  (a) Existing `platform-frontend-<hash>.run.app` URL — zero infra, accepts the .run.app domain
  (b) Short alias under `yourcompany.com` (DNS + Cloud Run domain mapping, ~30min)
  Recommendation: (a) for Tue/Wed dev. Decide (a) vs (b) by Wed EOD based on ONE's preference for
  Fri demo. Custom subdomain like `one.yourcompany.com` is post-demo.
- **~~Q2 — Document corpus content~~ RESOLVED.** ONE's real corpus exists at
  `gs://multivac-acme-energy-bucket` (in `multivac-acme-energy` project) with years of
  indexed PPAs, term sheets, and clause-segmented contracts. Same bucket is indexed by ONE's
  existing Vertex AI Search datastore (`one_generic` per v5 vac_config). No upload, no fixtures.
  Wire-up only. New mini-question: pick 2 contracts from `PPAs/longform/` for the M3 doc-compare
  demo; ideally same-template-derived for clean `block_id` alignment.
- **Q3 — Aitana admin sign-in on the ONE-branded deployment.** When Mark / Aitana admins sign in
  to debug, they see ONE branding (because branding is per-deployment). That's correct, but worth
  flagging: the admin views (skill creation UI, debug tools) still render under the ONE brand.
  Acceptable for the demo period; the Aitana commercial deployment (when it exists) will get its
  own URL with Aitana branding.
- **Q4 — PtX Tech Comparator data.** Hardcode SOEC/PEM/Alkaline values from the v5 prompt's tech
  catalog, or wire to a real source (BigQuery table, RAG over technology reports)? Recommendation:
  hardcode for the demo (4 rows × 6 columns from the v5 prompt). Data-driven later.
- **Q5 — Telegram channel.** v5 ONE deployment had Telegram wiring per the `vac_service.py`. Out
  of scope this sprint per Non-Goals — but worth a follow-up question: is the Telegram bot
  important to ONE's commercial conversation, or is web-only acceptable for the demo and a v6.5+
  task?
- **Q6 — Deferred ENTSO-E functions.** M2 ships only `entsoe_day_ahead_prices`. `entsoe_load` and
  `entsoe_generation_mix` (and any `deal_tracker.*` queries) deferred to v6.5 unless the demo
  rehearsal surfaces a question they'd answer. Acceptable risk: prices alone carry the valuation
  narrative.
- **Q7 — Diff alignment algorithm.** Block-level alignment relies on AILANG `block_id` matching
  when present. For unmatched blocks (likely 30–50% on dissimilar PPAs), text-similarity fallback
  is used. Acceptable diff quality for the demo? If not, the fallback's threshold + presentation
  ("low-confidence diff" badge) need tuning during M3 — track ~2h overrun risk on Thu.
- **Q8 — Workbench surface for single-doc clause extraction.** When the user runs
  `one-ppa-expert` and asks "extract clauses from contract X", should the Clause Extraction Card
  render inline in chat (default A2UI behavior) or mount to the workspace surface (richer, but
  competes with the doc-compare workbench for the same slot)? Recommendation: inline in chat for
  single-doc; workspace for the compare flow. Confirm on M2 Wed.

## Related Documents

- [docs/design/v6.3.0/implemented/client-tenant-management.md](../v6.3.0/implemented/client-tenant-management.md) — base tenant onboarding sprint this builds on
- [docs/design/v6.3.0/implemented/rag-document-corpus.md](../v6.3.0/implemented/rag-document-corpus.md) — sibling tenant feature, same bucket config used here
- [docs/design/v6.4.0/fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) — parallel sprint; this doc instantiates 4.1 M1+M4 with ONE content
- [docs/design/v6.4.0/SEQUENCE.md](SEQUENCE.md) — v6.4.0 ordering (this doc is 4.2)
- [docs/design/forks/tutoring-fork/v0.1.0/scope.md](../forks/tutoring-fork/v0.1.0/scope.md) — sister "downstream serves a vertical" pattern; the doc this sprint is consciously *not* following (no fork)
- [docs/product-axioms.md](../../product-axioms.md) — axiom scoring framework
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — verification log
- v5 ONE source: `<local-path>` — origin of the PPA vocabulary prompt
- v5 ONE Cloud Run: `acme-energy` in `multivac-internal-dev` (legacy, untouched by this sprint)

## Implementation Report

_To be filled in after the demo on 2026-06-12._

**Completed**: TBD
**Actual Effort**: TBD
**Branch/PR**: TBD

### What Was Built
- TBD

### Files Changed
- TBD

### Lessons Learned
- TBD
