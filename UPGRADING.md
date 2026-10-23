# Upgrading a fork after `make upstream-merge`

Most template changes merge down with nothing to do. When one needs action
in your fork, it gets an entry here — newest first. `make upstream-merge`
prints the entries a merge brings in, so read this before committing a merge.

Each entry: the date it landed in platform-source, who is affected, what you
will see, and the steps.

---

## 2026-10-23 — Public export contains only shared examples and documentation

Deployment search settings and A2A document buckets must be supplied through
`_VERTEX_AI_SEARCH_DATASTORE_ID`, `_VERTEX_AI_SEARCH_PROJECT`,
`_VERTEX_AI_SEARCH_LOCATION`, and `_A2A_AGENT_DOCUMENTS_BUCKET` Cloud Build
substitutions. The first, second and fourth default to empty; the location
defaults to `eu`. Configure the same values on branch and promotion triggers
before the next rollout. The document-analyst template inherits the deployment
datastore instead of pinning a particular datastore ID.

Private remote-worker tools that lack project-document isolation are refused
inside scoped project chats. Unscoped chats keep their existing policy.

Optional customer permission presets and local fixtures now live in private
extensions. Private forks retain their existing command interface and fixtures;
public forks get only generic user/domain/wildcard permission seeding and
workshop examples. Customer layout defaults can be set with
`NEXT_PUBLIC_WORKSPACE_DEFAULT_RATIOS`, a JSON map of skill IDs to ratios
between 0.3 and 1.0; otherwise every skill starts at the shared 0.5 default.

Internal runbooks, historical design records and development logs are withheld
from publication. Public user documentation remains in `docs/user-guide`.

---

## 2026-10-23 — Patched OpenTelemetry requires the coordinated ADK 2 stack

**Affected:** forks with the ADK 1.x backend or the pre-1.0 AG-UI client.

OpenTelemetry API/SDK and OTLP are pinned to 1.45.1 for CVE-2026-114603.
ADK 1.x imports the removed OTel Events API, so this also moves runtime/eval to
ADK 2.12, restores its GCP/MCP/A2A/database extras, and uses compatible GCP
exporters, aiplatform 2.6, ag-ui-adk 0.10 and frontend AG-UI client/core 1.0.3.
ADK 2.12 still caps OTel at 1.42.1; the uv override is deliberate and covered by
parser, import, real-Runner and real-client stream checks. MCP stays 1.x and A2A
stays 0.3. The existing LiteLLM security pin and other floors are preserved.

Merge backend and frontend together, run `uv sync --frozen` / `npm ci`, then
`make adk-conformance`, backend lint/tests and frontend `npm run quality:check`.
Run `make security-check`; no OTel advisory exception is needed. The real wire
check is documented in [03b](docs/design/template/template-adk-2-core-bump.md#03b-review-evidence-2026-10-23).

This PR is prepared for review; deployed migration gates remain pending.
Before rolling a fork's environment forward, complete the ADK project's dev
journeys, before/after eval, telemetry checks and cross-version session/artifact
round-trip in both directions. Treat ADK 2 session writes as one-way until that
fork's reverse-read/rollback check passes. Do not infer production readiness
from hermetic conformance alone.

---

## Dependency security

**2026-10-23 — runtime patches and enforced audit reporting.**

Run `make security-check` after resolving frontend, sandbox, backend or CLI
lockfiles. The npm gate checks production dependencies at high severity and
above; Python OSV audits report every severity. `AUDIT_INCLUDE_DEV=1 make
security-check` also scans npm development dependencies. A separate A2UI floor
rejects the affected prereleases that npm's advisory ranges can omit.

The frontend now uses the patched A2UI 0.10 React/core packages while retaining
the v0.9 wire API. Custom catalogs use `createComponentImplementation`, the
renamed component factory. Both the old Basic catalog URL and the SDK's new URL
resolve to the same patched functions, so stored surfaces remain renderable.
The matching Markdown plugin is pinned to 0.1.0;
DOMPurify and KaTeX overrides keep nested renderers on the patched versions.
Tailwind's animation plugin is a build dependency, not a server dependency.
The Vitest/Vite tooling is updated with the compatible React plugin/config.

The dependency-security backport preserved ADK 1.39.1 and LiteLLM 1.85.7,
including the security work in PR #78. Its follow-up above advances ADK with the
[ADK 2 project](docs/design/template/template-adk-2-upgrade.md) or its
[Phase 0 regression gate](docs/design/template/template-adk-2-regression-gate.md).
The coordinated upgrade above resolves the OpenTelemetry finding with the
patched stack and compatibility work, without an advisory ignore.

The Python gate has one explicit exception: the LiteLLM proxy-only advisory
(the application runs the SDK), preserving the reviewed decision in PR #78.
Never expose a LiteLLM proxy under this exception. Upstream already locks
Starlette 1.8.0, so the downstream's five Starlette exceptions are not copied.
No OpenTelemetry exception is added.

The scheduled workflow reports findings before failing the job. Missing
Dependabot permissions and scanner errors are failures, not empty inventories.
To enable its alert query/reconciliation, configure `DEPENDABOT_TOKEN` with
repository-specific Dependabot-alert read/write access; the default
`GITHUB_TOKEN` cannot access that API. No token is bundled with the template.
The reconciler defaults to dry-run and only dismisses alerts when every locked
copy is removed or outside all affected ranges, on the remote default-branch
HEAD. Unsupported ranges and prereleases stay open for manual review.

---

## 2026-10-01 — The template's default look is now the Sunholo brand

**Affected:** every fork that styled itself by editing `frontend/src/app/globals.css`
or `frontend/tailwind.config.ts` (Aitana, AIPLA). A fork that never touched
them simply takes the new look.

**What changed upstream**
- `globals.css` tokens are now the Sunholo palette (Action orange primary,
  ink/slate neutrals, `--radius: 3px`) with a derived dark mode, plus
  `--brand-accent` / `--brand-slate` and `--code-*` highlight colours.
- New global rules: bold `h1`–`h3`, a visible `:focus-visible` ring, flat
  surfaces (`.shadow-*` → none), underlined primary-colour prose links,
  inline-code styling, the header wordmark in `--brand-slate`.
- Montserrat is loaded via `next/font` in `app/layout.tsx` and `font-sans`
  reads `var(--font-sans)`.
- The Tailwind radius scale maps every step (`sm`…`2xl`) to `--radius`.

**What you will see when you merge**
- Conflicts in `globals.css` (the `:root` / `.dark` token block) and
  `tailwind.config.ts` (`fontFamily`, `borderRadius`), if you edited them.
- Without a conflict, your app also picks up the new rules above: shadows
  gone, bold headings, the focus ring, Sunholo-coloured code, and `rounded-xl`
  following `--radius`.

**What to do** (once; afterwards style changes upstream never conflict again)
1. Resolve both conflicts by taking **upstream's** version of
   `globals.css` and `tailwind.config.ts` whole.
2. Create `frontend/public/brand/<your-fork>/theme.css` and put **your** values
   there — your old `:root` / `.dark` token values, `--radius`, your font
   (`@font-face` + `html body { font-family: … }`), and anything from the list
   above you want back (e.g. shadows). Use `html:root` / `html.dark` selectors.
   Worked example: `frontend/public/brand/README.md`.
3. Set `NEXT_PUBLIC_BRAND_THEME_CSS=/brand/<your-fork>/theme.css` in your
   build env (Cloud Build FIREBASE_ENV secret or `.env.production.local`), and
   add `frontend/public/brand/<your-fork>/*` to `.customer-paths` so
   `make port-up` never ships it upstream.
4. Check light and dark mode, phone and desktop, before deploying.
