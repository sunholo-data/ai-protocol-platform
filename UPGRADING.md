# Upgrading a fork after `make upstream-merge`

Most template changes merge down with nothing to do. When one needs action
in your fork, it gets an entry here — newest first. `make upstream-merge`
prints the entries a merge brings in, so read this before committing a merge.

Each entry: the date it landed in platform-source, who is affected, what you
will see, and the steps.

---

## 2026-11-09 — The MCP server refuses anonymous calls by default

**Affected:** every deployed fork that never set `MCP_AUTH`. Until now it
defaulted to `optional`, so `/mcp` (reached as `/api/proxy/mcp` through the
frontend) let anyone on the internet list and run your public skills, at
your model cost and with whatever data their tools reach.

**What you will see when you merge**
- An MCP call without a token gets `401` with a `Bearer` challenge. Signed-in
  callers are unaffected: a personal MCP token (`aip_mcp_…`, Settings) or a
  Firebase ID token works as before.
- `LOCAL_MODE=1` still defaults to `optional`, so laptop and workshop runs
  are unchanged.
- Only the exact value `optional` opens the endpoint; any other value,
  including a typo, keeps it closed.

**What to do**
1. Nothing, to take the safe default.
2. If a deployed env serves an open demo on purpose, set `MCP_AUTH=optional`
   on it, and check what its public skills' tools can reach first.
3. Tell anyone who calls your skills from a script or an MCP client to mint
   a personal MCP token instead of calling anonymously.

---

## 2026-11-06 — Model choice in the chat: Auto, Fast, Thorough, specific models by opt-in

**Affected:** every fork gets the menu with no action. Forks with a routing
front door or a skill named after its model should opt those skills out
(step 2). Every fork picks up the `oauthlib` security floor.

**What you will see when you merge**
- The composer's **+** has a **Model** row: Auto (the default, the skill's
  own model), Fast and Thorough, each naming the model it uses. Any choice
  other than Auto shows a chip beside the + with × back to Auto. The choice
  is saved per chat (`chat_sessions.modelChoice`) and a new chat starts on
  Auto.
- Settings has a **Models** section: *Choose a specific model* (off by
  default, per browser) adds the models this deployment allows to the menu.
- A pick applies to every agent in the turn's tree, including delegates, and
  replaces a skill's `thinkingModel` router. Residency, fallbacks and budgets
  apply as usual: an `eu-strict` deployment only ever lists and runs EU
  models, and a provider whose API key isn't mounted is left off the list.
- On an EU-only deployment Thorough is hidden for skills on `pro` with
  dynamic thinking: `smart` and `pro` are the same EU model today, and
  Gemini 3.x high thinking measured no deeper than dynamic. It reappears
  when `smart`'s `eu-strict` variant changes.
- New: `GET /api/models/choice`, the `choice:` block in
  `backend/config/models.yaml`, `adk/model_choice.py`, and
  `ThinkDepth.HIGH`. Design note: `docs/design/v6.42.0/model-choice.md`.

**What to do**
1. Nothing, to take the default (`MODEL_CHOICE=all`). To narrow it, set
   `MODEL_CHOICE=tiers` (no specific models) or `MODEL_CHOICE=off` (no
   Model row) in the deployment's environment.
2. Add `modelChoice: false` to the SKILL.md metadata of any skill that must
   keep its model: a front door that routes (its delegates still follow the
   pick), a skill named after its model, or a skill tuned on one model. The
   template already opts out `gpt-assistant`, `claude-assistant`,
   `openai-reasoner` and `data-extractor`. Skills live in Firestore, so
   re-seed or `skill push` them after the merge.
3. If your fork edits `models.yaml`, check the `choice.pickable` list: it
   names registry ids and the label people see. List a global entry before
   its EU sibling (they are de-duplicated by label).
4. `backend/pyproject.toml` now requires `oauthlib>=4.0.1`
   (CVE-2026-49272). Re-lock if your fork keeps its own `uv.lock`.

---

## 2026-11-05 — A simpler chat screen: one sidebar search, the composer's +, one Settings page

**Affected:** every fork gets the new UI with no action. A fork with its own
code against the chat UI should check the list of removed pieces below.

**What you will see when you merge**
- The sidebar has one search ("Search chats and files", with the skill
  filter inside it), then Projects (three, then "All projects"), Chats (one
  line each, with a single ⋯ menu: Rename, Move to project, Delete…) and
  Files (the three recent files, then "Browse all folders").
- The message box has one **+** for upload, your files, a project and "Read
  replies aloud" (a bottom sheet on phones). A new chat shows the greeting,
  the message box and up to four of the skill's example prompts.
- Each reply opens with a one-line summary of its steps and has Copy / Read
  aloud under it. Workbench tabs are the open results plus Activity; "All
  results" lists everything from the chat. Skills with launchers or example
  documents keep them on a "Start" tab. Phones lose the Chat/Workbench
  switch: a result card opens the result, "Back to chat" returns.
- `/settings` is one page (instructions, memory, voice and alerts,
  connections); `/settings/instructions|memory|mcp` redirect to its
  sections. The account menu is Settings, Projects, Help, Admin, Sign out;
  Edit / Create skill moved to the bottom of the skill picker.
- A branded 404 page; `/workshop` 404s unless the local-mode banner is on.
- `frontend/CLAUDE.md` lists the UI kit and five rules for UI code, and the
  `ui-guidelines-check` skill checks a UI diff against Vercel's interface
  guidelines.

**Removed:** `AutoReadToggle`, `RecentConversations`, `WorkbenchHome`,
`WorkbenchIndex` (chat) and `NotificationsToggle` (navigation), and
`UserMenu`'s `items` prop.

**What to do**
1. If fork code imports a removed component or passes `UserMenu` `items`,
   move it to the replacement above (the + menu, the sidebar's Chats,
   `AllResultsMenu`, Settings' "Voice and alerts").
2. If your fork's user guide or screenshot scripts describe the old sidebar
   ("Your files", "Past conversations"), the template's
   `docs/user-guide/` and `scripts/user-guide/shots/` show the new wording.

---

## 2026-11-03 — Shared UI kit; the template theme is the Sunholo brand again

**Affected:** a fork whose look still lives in `frontend/src/app/globals.css`
or `tailwind.config.ts` rather than its own `public/brand/<fork>/theme.css`
(Aitana: its palette and Euclid Circular B font came upstream with the
2026-10-19 catch-up and are now removed from the template). Every fork gets
the UI changes below with no action.

**What you will see when you merge**
- `globals.css` and `tailwind.config.ts` return to the 2026-10-01 Sunholo
  tokens and `var(--font-sans)` font stack. A fork relying on the values
  that were there will look Sunholo-red until it does step 1.
- New `frontend/src/components/ui/` (Button, Spinner, Skeleton, Badge,
  `useConfirm`, `notify` toasts) and `useAsyncAction`; new `--success`,
  `--warning`, `--info` tokens; a global `prefers-reduced-motion` rule.
- One header on every page outside chat; Edit / Create skill moved into the
  account menu; the chat sidebar opens by default on wide screens.
- `fetchWithAuth` times out after 60s without a response (`timeoutMs` to
  change it, `0` to turn it off for a slow endpoint).
- `/dev/*` pages 404 in production builds.

**What to do**
1. If your fork's colours or font were in `globals.css`, follow steps 1–3 of
   the 2026-10-01 entry below: take upstream's `globals.css` and
   `tailwind.config.ts`, and put your `:root` / `.dark` values, `--radius`
   and `@font-face` rules in `public/brand/<your-fork>/theme.css`.
2. Optional: give `--success` / `--warning` / `--info` your own values there.

---

## 2026-11-02 — Knowledge-base files our parser can't read go to Vertex's parser

Knowledge-base sources (folder, GCS, git, GitHub) affected. A PDF with no text
layer, such as slides exported as pictures, used to fail with
`PDF_BACKEND_FAILED`. Now, when AILANG Parse finds no text in a `.pdf`,
`.docx`, `.pptx` or `.xlsx`, the sync copies the file to
`gs://<bucket>/knowledge-files/<kb>/` and has the datastore's own layout
parser read it. Expect a few minutes before it is searchable; the next sync
confirms it (`datastoreParsing` in the sync summary).

1. **The bucket.** Set `KNOWLEDGE_FILE_BUCKET`, or leave it unset to use
   `DOCUMENTS_BUCKET`. With neither set, nothing changes: such files stay
   failed, as before.
2. **Read access for the datastore.** The Discovery Engine service agent,
   `service-<project-number>@gcp-sa-discoveryengine.iam.gserviceaccount.com`,
   needs `roles/storage.objectViewer` on that bucket. Without it, the import
   is accepted but the parse is refused, and the file shows as failed with the
   datastore's reason.
3. **Retry the files that failed before.** A sync retries them whenever it
   lists their source. A GitHub source is listed only when its branch moves,
   so to retry sooner, run the operator sync with `--force`
   (`python -m private.knowledge.sync --config <file> --kb <id> --force`).

## 2026-10-30 — Projects and knowledge bases can sync from GitHub (opt-in)

Every fork's project page gains a **Sources** section, and the CLI gains
`aiplatform project source add|list|remove` and `aiplatform project sync`. A
project can follow one of the owner's library folders with no configuration.
Following a GitHub folder, and keeping it current after each push, needs three
settings. Until a fork adds them, the section explains that the user has no
GitHub access, and nothing else changes.

1. **GitHub access, and who may use it.** Set `GITHUB_SOURCE_CREDENTIALS` on the
   backend (JSON, or the path of a JSON or YAML file) to name each credential,
   the repositories it may read (`owner/name` globs, where `!owner/name` leaves
   one out) and the people who may use it (the platform's access rule:
   `tagged`, `specific`, `domain` or `public`; leaving it out means nobody). Both are checked when a source is added and
   again on every sync. Without the `users` rule, anyone who can create a project
   could read every repository the credential reaches. Prefer a **GitHub App**
   (`app_id`, `private_key_env`, optionally `installation_id`) with only
   *Contents: read*, installed on your own organisation. Each sync gets a
   one-hour token for one repository, and nothing depends on a person's
   account. A fine-grained token with *Contents: read* (`token_env`) also works.
   Keep the private key and any token in Secret Manager. Never share one
   deployment's App installation with another tenant's deployment: each
   tenant installs its own.
2. **The push trigger.** Create a Cloud Scheduler job that runs every 5 minutes
   and sends `POST <backend>/internal/sources/sync-due` with an OIDC token
   minted as a dedicated service account. Set `SOURCES_SCHEDULER_OIDC_SA`
   (that account's email) and `SOURCES_SCHEDULER_AUDIENCE` (the URL) on the
   backend. If either is missing, the route refuses every call. A
   host-run fork can instead call `python -m sources.scheduler` from a timer in
   `backend/`. Without a trigger, sources sync only when someone presses
   **Sync now**. Optionally, a deployment that GitHub can reach can also add a
   repository webhook for `push` events to `POST <backend>/api/webhooks/github`,
   with `GITHUB_WEBHOOK_SECRET` set on the backend. A push then syncs within
   seconds, and the scheduler stays as the backstop.
3. **Knowledge bases (forks with the knowledge-base extension).** A KB source
   in your sources config can use `type: github` with `repo`, `ref`, `path` and
   `credential_ref` (a credential above; its `repos` apply) in place of a `git`
   source's `repo_path`. It keeps the same
   document identities, so switching a source re-indexes nothing. Optional
   `page_url`, `page_root` and `page_style` make citations link to the
   published page.

---

## 2026-10-23 — Keep Vertex chat lookup bounded after the ADK 2 upgrade

The platform's Vertex chat path retains its existing native-ID contract:
AG-UI thread IDs equal stored session IDs, including bootstrapped sessions.
It now uses direct, owner-checked reads rather than ag-ui-adk 0.10's general
alias-recovery scan. Listing all session state on each new chat can exhaust
container memory for users with large histories. Request-owned session manager
cleanup tasks are stopped when the stream ends or is cancelled.

No session migration or deletion is performed. Existing native-ID chats and
unstamped bootstrapped sessions resume normally. A foreign owner, conflicting
app/thread stamp, invalid ID or failed storage read is an error; it never
creates a replacement conversation. Forks that previously opted into
generated-ID/alias sessions must retain their own alias resolution instead of
using this native-ID chat path. Non-Vertex services keep upstream resolution.

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

**2026-10-31 — pyasn1 0.6.5.** The backend floor rises from 0.6.4 to 0.6.5
for CVE-2026-119194 and CVE-2026-119197, which made the backend pip-audit gate
fail on every PR. Only pyasn1 changes in `backend/uv.lock`; the CLI does not
depend on it.

**2026-10-26 — compatible Zod 3 security backport and dependency floors.**

The backend and CLI also require Certifi 2026.10.23. Its published CA bundle
removes emSign G3, C1 and C3; gRPC's embedded bundle and the system CA directory
can still retain them. `config.tls` configures consistent HTTP/gRPC trust
defaults, and Docker selects them before Python starts. Explicit operator CA
overrides are preserved. The default OpenSSL CA directory is Certifi's package
directory, preventing lazy fallback to distrusted system roots. Three tests
check actual certificate fingerprints, directory selection and overrides;
real Google HTTPS and gRPC TLS handshakes also pass. See
[Certifi's CA change](https://github.com/certifi/python-certifi/commit/b60466c9f2882d9cf088943c650b0faac06c080c).

The dependency floors cover body-parser 2.4.0, undici 7.30.1, fast-uri 3.1.9,
ip-address 10.7.4, and filelock 4.2.1 or newer. The root Zod dependency is
explicitly on patched 4.6.7; legacy A2UI and AG-UI consumers retain Zod 3's
schema and introspection APIs. A global Zod 4 override breaks A2UI binding.

`frontend/scripts/patch-legacy-zod.mjs` backports the fail-before-element-walk
behavior of [Zod e06a510](https://github.com/colinhacks/zod/commit/e06a510b6fc1e6e28e3201fd116ca2a76212f6be)
to Zod 3 arrays/sets with declared upper bounds. It also applies an input
budget before sync, async and Standard Schema validation: 10,000 values,
64 levels, and 2,097,152 aggregate string/key characters. This bounds the
unbounded schemas left unchanged by the upstream Zod 4 fix. Oversized inputs
return a validation error; they are not truncated. Review these intentional
limits against deployment workloads. Trusted schema transforms remain code,
not a sandbox for untrusted programs.

The backport covers every locked legacy copy, including the v3 entry point
bundled in Zod 4, in ESM and CommonJS. Installation fails on unknown source,
versions or patch output: original and patched SHA-256 hashes are committed.
`npm ci` applies it through `postinstall`, also in the Docker builder.

Raw npm reports still list Zod 3's package version. The security gate retains
those reports and accepts this one advisory as **BACKPORTED** only after a
fresh isolated production install, byte verification and 38 real runtime
checks. Other advisories, unexpected copies, missing build hooks and scan
errors still block; seven gate regressions cover these cases. The existing
Python exception policy is unchanged. No legacy OpenTelemetry patch or
deployment configuration is added to this ADK 2 repository.

Remove the source backport when compatible upstream consumers use a patched
release and equivalent input bounds are verified. Keep the renderer/action
contracts and hostile-input tests in the upgrade gate.

**2026-10-23 — frontend Node runtime alignment.**

Use Node 22.12 or newer for frontend development and builds. The frontend's
builder and production container, GitHub CI and Cloud Build Node steps now use
Node 22. Patched HTTP dependencies require the global File API; a Node 18
container can pass compilation but fail Next.js prerendering with
`ReferenceError: File is not defined`. The package engine floor makes the
runtime requirement explicit. No document or session migration is involved.


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

The Python gate has one explicit exception: LiteLLM's proxy-only advisories
(the application runs the SDK), preserving the reviewed decision in PR #78.
Never expose a LiteLLM proxy under this exception. Upstream already locks
Starlette 1.8.0, so the downstream's five Starlette exceptions are not copied.
No OpenTelemetry exception is added.

**2026-10-28 — dnspython 2.9.0; a second LiteLLM proxy-only advisory.**
`dnspython` moves to 2.9.0 for CVE-2026-114605 (unbounded compression-pointer
chains). GHSA-g5ff-637f-6q2m is a file read through the LiteLLM proxy's
`/utils/transform_request` endpoint; nothing here runs that proxy, so it joins
PYSEC-2026-4066 under the same exception rather than moving #78's reviewed pin
from 1.85.7 to 1.95.0. Review both together when LiteLLM is next upgraded.

**2026-10-27 — filelock 4.2.4.** `filelock` moves to 4.2.4 for its three
advisories; huggingface-hub's locks were exercised on it. A time-limited npm
exception for GHSA-mrhx-c7xc-m3w5 landed the same day and was withdrawn on
2026-10-28 in favour of the verified Zod 3 backport above, so no npm advisory is
excepted. Do not override to zod 4: `@a2ui/web_core`'s field unwrapping reads
`_def.typeName`, which zod 4 no longer sets, so component-id and child-list refs
inside optional, nullable or default fields would silently stop being found.

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
