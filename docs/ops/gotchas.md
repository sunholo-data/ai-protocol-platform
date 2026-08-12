# Platform Gotchas

Operational surprises that have burned us or the AIPLA fork. Each entry explains the symptom, root cause, and fix.

---

## Firebase resource-location must be set before first Firestore write

**Symptom:** `FirebaseError: Installations: Create Installation request failed with error "400 INVALID_ARGUMENT: Firebase resource location is not set."` — seen only in a fresh GCP project, usually in CI or when the fork bootstraps a new environment.

**Root cause:** Firebase requires `google_app.options.databaseURL` or an explicit resource-location to be set before the Firestore SDK can initialise. In the Aitana v6 / AIPLA template the Firebase Admin SDK picks up the app's resource location from the service account, but a brand-new project has no location until the first call to `firebase init` or until a resource (Firestore database) is created via the console / Terraform.

**Fix (template forks):**
1. Create the Firestore database in the correct region **before** the backend service starts. In Terraform: `google_firestore_database` with `location_id = "eur3"` (or your region).
2. If using the bootstrap script (`scripts/bootstrap-gcp-project.sh`), ensure the Firestore step runs before any Cloud Run deploy.
3. Set `FIRESTORE_EMULATOR_HOST=localhost:8080` in local dev so the SDK never hits production before the database exists.

**See also:** [template-cloudbuild-hardening.md](../design/template/template-cloudbuild-hardening.md) — item #9 in the upstream feedback.

---

## Empty user_email causes Firestore 400 on tool_permissions lookup

**Symptom:** `400 InvalidArgument: Document name must not be empty` when the permission enforcer looks up an anonymous user's tool access.

**Root cause:** `auth.permissions.can_use_tool()` previously passed `user_email=""` directly to `fs.get_document("tool_permissions", "")`, producing a Firestore path with a trailing slash (`tool_permissions/`).

**Fix:** Guard on empty email before the user-level lookup — see `backend/auth/permissions.py`:
```python
user_doc = fs.get_document(COLLECTION, user_email) if user_email else None
```
The lookup falls through to domain → wildcard as expected.

---

## Anonymous-group users trigger Firestore permission-denied console errors

**Symptom:** Dozens of `FirebaseError: Missing or insufficient permissions` in the browser console when running in `NEXT_PUBLIC_AUTH_MODE=anonymous_group_id` mode.

**Root cause:** `useDocBrowser`, `useDocument`, and related hooks called `onSnapshot()` unconditionally. Anonymous-group sessions have no Firebase Auth user, so Firestore rules deny the snapshot immediately — and the SDK retries forever, flooding the console.

**Fix:** Import `isAnonymousGroupAuthMode` and return early before calling `onSnapshot`:
```typescript
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
if (!db || !uid || isAnonymousGroupAuthMode()) return;
```
Applied to `useDocBrowser.ts` (both folder and document listeners) and `useDocument.ts`.

---

## Cloud Build secrets fail the deploy when a channel is disabled (#5)

**Symptom:** `gcloud run deploy` exits with `Secret Manager secret ... does not exist` even though the channel (Telegram, WhatsApp, email) is not being used.

**Root cause:** The original `cloudbuild.yaml` passed `--set-secrets=TELEGRAM_BOT_TOKEN=...` unconditionally. If the secret doesn't exist in Secret Manager the deploy step fails before the service starts.

**Fix:** The deploy step now uses channel feature flags (`_ENABLE_TELEGRAM`, `_ENABLE_WHATSAPP`, `_ENABLE_EMAIL`, `_ENABLE_ANTHROPIC`). Set any flag to `'false'` (default) and the corresponding `--set-secrets` flag is omitted entirely. To activate a channel, create the secret in Secret Manager and set the flag to `'true'` in the Terraform Cloud Build trigger substitutions.

---

## Cloud Build log bucket must exist before first deploy (#6)

**Symptom:** Cloud Build fails at the start with `bucket does not exist` or access-denied writing logs.

**Root cause:** The original `cloudbuild.yaml` hardcoded `gs://your-deploy-project-id-logging-bucket` — an Aitana-owned bucket that forks don't have access to.

**Fix:** `logsBucket` now uses the `_LOG_BUCKET` substitution which defaults to `gs://${_PROJECT_ID}-cloudbuild-logs`. Create this bucket by running:
```bash
./scripts/bootstrap-gcp-project.sh <project-id> <runtime-sa-email>
```
The bootstrap script creates the bucket and grants the Cloud Build SA write access.

---

## Cloud Build service agent not auto-provisioned in new GCP projects (#7)

**Symptom:** `gcloud builds triggers create` or `gcloud run deploy` via Cloud Build fails with `INVALID_ARGUMENT` on projects created after mid-2024.

**Root cause:** Google stopped auto-provisioning the `service-<project-number>@gcp-sa-cloudbuild.iam.gserviceaccount.com` service agent on new projects. Without it, Cloud Build cannot act as the runtime SA or create triggers.

**Fix:** Run once before the first trigger creation:
```bash
./scripts/bootstrap-gcp-project.sh <project-id> <runtime-sa-email>
```
This materializes the agent via `gcloud beta services identity create` and grants it `iam.serviceAccountUser` on the runtime SA.

---

## Cloud Build v2 repository registration requires GitHub `admin` permission (#8)

**Symptom:** `gcloud builds repositories create` fails with an error naming the GitHub user but giving no hint about the missing permission.

**Root cause:** Setting up server-side webhooks requires `admin` on the GitHub repository. `push` permission is not enough.

**Fix:** Promote the CI bot account (or the authorizing GitHub user) to `admin` on the repository before running `gcloud builds repositories create`. For a dedicated bot, use a GitHub App with `admin:repo_hook` scope rather than a personal access token.

---

## `gcloud auth print-identity-token` fails under user-managed SA (#13)

**Symptom:** The `seed-platform-skills` Cloud Build step silently succeeds (`exit 0`) but the marketplace is empty. The step log shows `TOKEN=` with an empty value or an error that was swallowed.

**Root cause:** `gcloud auth print-identity-token --audiences=...` only works when Cloud Build runs under the default Compute SA. Under a user-managed SA it returns an error; the original step had `set +e; exit 0` so the failure was invisible.

**Fix:** The seed step now uses the GCE metadata server endpoint:
```bash
TOKEN=$(curl -sf \
  "http://metadata.google.internal/.../identity?audience=<URL>&format=full&include_email=true" \
  -H "Metadata-Flavor: Google")
```
This works under any SA. The step also no longer has `set +e; exit 0` — a non-200 response now fails the build visibly.

---

## Identity token missing `email` claim causes silent 403 (#14)

**Symptom:** Even after fixing #13, the seed step returns 403 with no useful body. The backend logs show nothing diagnostic.

**Root cause:** The GCE metadata server omits the `email` claim by default unless `include_email=true` is in the request query string. The backend's admin allowlist check required the claim.

**Fix (two-part):**
1. Append `&include_email=true` to the metadata server URL (see #13 above — already in the fixed seed step).
2. The backend now logs a clear error when the claim is absent:
   ```
   admin_auth_denied: email claim absent from token — did you forget include_email=true?
   ```
   The 403 detail also says `"email claim missing from token"` instead of the generic `"Not authorized"`.

---

## tool_permissions wildcard doc must be seeded on first deploy

**Symptom:** All tool calls are silently denied for every user in a fresh environment because there is no `tool_permissions/*` document.

**Root cause:** The permission enforcer falls through to "deny" when no user/domain/wildcard doc exists. A fresh project has no seed data.

**Fix:** `platform_seed.py` (called once per deploy by Cloud Build) now calls `_ensure_tool_permissions_wildcard()` which idempotently writes a `{"type":"wildcard","tools":["*"],"denied":[]}` doc if none exists. See `backend/admin/platform_seed.py`.

---

## A2UI `updateDataModel` without `path` defaults to root-REPLACE (G25)

**Symptom:** A multi-stage skill (extractor → validator → poster) populates a workspace surface card. Each stage looks correct in isolation. The final card shows **only** the last stage's fields — everything from earlier stages has been silently overwritten. Single-stage skills (the `workspace-demo` starter) don't see this because there's nothing earlier to clobber.

**Root cause:** A2UI v0.9's `updateDataModel.value` with no `path` field defaults to `path: "/"`, which calls `surface.dataModel.set("/", value)` — replacing the entire data model on the named surface. Every stage that omits `path` clobbers everything previously written there.

**Fix:** In multi-stage skills, emit **one `updateDataModel` per field** with explicit JSON Pointer paths:

```json
[
  { "version": "v0.9", "updateDataModel": { "surfaceId": "workspace", "path": "/status",  "value": "VALID" } },
  { "version": "v0.9", "updateDataModel": { "surfaceId": "workspace", "path": "/verdict", "value": "matches vendor master" } }
]
```

Each `set("/status", "...")` patches a single leaf; siblings are preserved. Root-replace (no `path`) is correct **only** when the previous stage's data is no longer relevant — typically a single-stage skill resetting its own surface.

**Authoring rule for SKILL.md prompts:** if your skill is part of a pipeline (declares `subSkills:` or is summoned by a `SequentialAgent`), use per-path patches. If it's a single chat-driven demo (e.g. `workspace-demo`), root-replace is fine.

**See also:** [template-protocol-defaults.md G25](../design/template/template-protocol-defaults.md) — the design doc behind this default. Surfaced by gde-ap-agent fork during AP demo polish (2026-06-03).

---

## MCP App artefact changes need their own Cloud Build trigger (G37)

**Symptom:** Push a frontend / backend change to `dev` → it deploys. Push a change to `infrastructure/mcp-sandbox/artefacts/**` → host shell still updates, but when you open the embedded iframe you see the OLD artefact. The fork user assumes the deploy didn't happen.

**Root cause:** The `mcp-sandbox` service is a separate Cloud Run service with its own [`infrastructure/mcp-sandbox/cloudbuild.yaml`](../../infrastructure/mcp-sandbox/cloudbuild.yaml). The main Cloud Build trigger watching the repo's root only fires `cloudbuild.yaml` (the frontend + backend pipeline). Without a SECOND trigger watching `infrastructure/mcp-sandbox/**`, artefact edits never reach the deployed sandbox.

**Fix:** Create a dedicated trigger per environment, included-files-scoped to the sandbox path:

```bash
gcloud builds triggers create github \
  --name="mcp-sandbox-dev" \
  --service-account="projects/<PROJECT_ID>/serviceAccounts/<RUNTIME_SA>" \
  --build-config="infrastructure/mcp-sandbox/cloudbuild.yaml" \
  --included-files="infrastructure/mcp-sandbox/**" \
  --branch-pattern="^dev$" \
  --project="<PROJECT_ID>" --region="<REGION>"
```

The `--included-files` glob means the trigger only fires on artefact edits — it doesn't waste a build slot on every frontend/backend push.

**Recovery if you've already pushed artefact changes:** manually submit the build to flush the queue:

```bash
gcloud builds submit infrastructure/mcp-sandbox \
  --service-account=projects/<DEPLOY_PROJECT>/serviceAccounts/<SAME_SA_AS_THE_TRIGGER> \
  --config=infrastructure/mcp-sandbox/cloudbuild.yaml \
  --substitutions=_PROJECT_ID=<PROJECT_ID>,_REGION=<REGION>,_ARTIFACT_REGISTRY_REPO_URL_CLIENT=<URL>
```

> **`--service-account` is not optional here (AIPLA #46).** `builds submit`
> without it silently falls back to the **Compute Engine default SA**, so this
> recovery command runs as a *different identity* than the trigger it is
> standing in for — and any IAM grant covers exactly one of them. The symptom is
> a `PERMISSION_DENIED` on an Artifact Registry read, which sends you hunting in
> the registry grant rather than at the identity. Name the same SA the trigger
> uses.
>
> Two more things `builds submit` needs that the trigger path never reveals:
> it uploads your **local working tree** as the build source (so what you ship
> depends on what is checked out — see the promotion runbook for why we never
> use it in the release path), and it stages a tarball in the auto-created
> `<project>_cloudbuild` bucket, which the named SA must be able to read back
> (`roles/storage.objectViewer`).

`scripts/bootstrap-gcp-project.sh` prints the trigger-create command in its Next-steps block — re-run the relevant `gcloud builds triggers create` once per environment to fix.

**See also:** [template-cloudbuild-hardening.md G37](../design/template/template-cloudbuild-hardening.md) + [template-mcp-apps-artefact-quality.md](../design/template/template-mcp-apps-artefact-quality.md). Surfaced by gde-ap-agent fork (2026-06-03) when an artefact retheme appeared not to land.

---

## In-process MCP servers must be seeded with loopback URLs, NEVER the public Cloud Run URL (G42)

**Symptom:** A SKILL.md declares `tool_configs.mcp.servers: [vendor-master]`. The corresponding `mcp_servers/vendor-master` Firestore doc exists and looks correct. But every chat that triggers the agent fails with `"Tool 'lookup_vendor' not found"` (or the agent silently behaves as if the MCP toolset has zero tools). Slow first-click on the chat too — the McpToolset is timing out trying to handshake before giving up.

**Root cause:** The `mcp_servers/<name>.url` field was seeded with the **public Cloud Run URL** of this service. The `McpToolset` that consumes the seed runs inside THIS Python process, so it dials the URL to fetch tools — but on Cloud Run the public hostname routes to the FRONTEND container, and `next.config.mjs` has no rewrite for `/mcp/*`. Every MCP session creation 404s; the agent boots without the toolset's tools; the LLM (per its SKILL.md) calls them anyway; ADK crashes the run. (Pre-G41 this then triggered a duplicate-terminal-event crash in the frontend AG-UI state machine, compounding the confusion.)

**Fix:** When seeding `mcp_servers/` docs that point at THIS service's in-process FastMCP mounts, use **loopback** (`http://127.0.0.1:<PORT>/mcp/<name>/`), not the public Cloud Run URL. PORT is whatever uvicorn binds to — `$PORT` on Cloud Run, `1956` locally.

The platform ships a helper that derives the right URL — use it instead of building strings by hand:

```python
from tools.mcp.registry import derive_in_process_mcp_base_url

base = derive_in_process_mcp_base_url()  # http://127.0.0.1:1956 (or $PORT on Cloud Run)
config = {
    "name": "Vendor Master",
    "url": f"{base}/mcp/vendor-master/",
    "transport": "http",
}
fs.set_document("mcp_servers", "vendor-master", config)
```

Ops can override via `MCP_INTERNAL_BASE_URL=<url>` for test fixtures or alternate binds. **Never** seed a public URL even via override — that breaks the contract.

**External MCP servers** (separate Cloud Run services, third-party MCP endpoints) are different and DO need their public URL in `mcp_servers/`. The loopback rule applies only to in-process servers mounted on THIS service's FastMCP.

**Recovery if you've already seeded a public URL:** delete the doc + re-run your seed code with `derive_in_process_mcp_base_url()`. If the seed runs at startup, just redeploy — the next boot will write the correct URL.

**See also:** [template-mcp-strict-resolution.md](../design/template/template-mcp-strict-resolution.md) part (a). Surfaced by gde-ap-agent fork (2026-06-06): cascade was "Tool 'lookup_vendor' not found" → slow first-click → ADK crash → `RUN_FINISHED-after-RUN_ERROR` frontend state-machine crash (now suppressed by G41).

## Skill config changes need a SEED, not a code deploy (and can be silently preserved)

**Symptom:** you change a `SKILL.md` — model tier, description, instructions,
`welcome.example_prompts` — push, watch the build go green, test on the deployed
env, and get the OLD behaviour. You conclude the fix didn't work and start
debugging the wrong thing. (Cost real time on 2026-07-17: a `lite`→`pro` bump was
tested against the old model twice because Firestore still said `lite`.)

**Cause:** skills are read from **Firestore**, not from the container's
`SKILL.md`. The templates only reach Firestore via the platform-skill **seed**
(`/api/admin/seed-platform-skills`), which runs as a **non-fatal step in
`backend/cloudbuild.yaml`** — i.e. on the BACKEND trigger. So:

- A change to a `SKILL.md` takes effect when the **backend** build's seed step
  runs — *not* when the frontend/sidecar deploys.
- The seed is **non-fatal**: the build reports SUCCESS even if the seed failed.
  Check the step's log, not the build status:
  ```
  gcloud builds log <id> --project=your-deploy-project-id --region=europe-west1 \
    | grep "seed-platform-skills ->"
  # 200 body={"created":0,"skipped":0,"failed":[],"refreshed":16,"preserved":0}
  ```
- **`refreshed`** = template fields pushed to existing skills (what you want).
  **`preserved`** = skipped because the skill is `managed_by: "firestore"` — i.e.
  someone SAVED it in Skill Studio, making Firestore authoritative. A preserved
  skill will **never** pick up your SKILL.md edit, silently. If `preserved > 0`
  and your change didn't land, that's why.

**Verify the value actually landed** rather than trusting the seed report:
```bash
TOK=$(gcloud auth print-access-token)
curl -sS -H "Authorization: Bearer $TOK" \
 "https://firestore.googleapis.com/v1/projects/your-project-id-<env>/databases/(default)/documents/skills/<skillId>" \
 | python3 -c "import sys,json;f=json.load(sys.stdin)['fields'];m=f['skillMetadata']['mapValue']['fields'];print('model:',m.get('model',{}).get('stringValue'))"
```

Note Firestore stores the welcome block **snake_case** (`example_prompts`,
`example_documents`); the model's alias serialisation converts to camelCase for
the frontend. Querying for `examplePrompts` in Firestore returns nothing and
looks like a bug — it isn't.

## A tag-push trigger with `includedFiles` never fires (v6.20.0, ARTIFACT-PROMOTION M4)

**Symptom:** you create a Cloud Build trigger on `push.tag: ^v.*$`, push a
version tag, and *nothing happens*. No build, no error, no entry in
`gcloud builds list`. The trigger is enabled, the tag is on origin, the pattern
matches — and it still does not fire.

**Cause:** the trigger carries `includedFiles` (path filters). Those filters
match against the **file diff** of a push, and a tag push carries no diff for
them to match, so every event is filtered out. The filters are silently
correct-looking and silently fatal.

This is easy to walk into because the natural way to create a release trigger is
to copy the branch trigger and change `branch:` to `tag:` — which is exactly how
we hit it. The branch trigger *should* have path filters (they stop a docs-only
commit rebuilding three images on every push to `dev`); the tag trigger must
not.

**Fix:** drop `includedFiles` entirely on tag triggers. It is also the right
semantics: a release builds the whole tagged tree. If you tag a commit that only
touched docs, you still want the release artifacts to exist for that version —
otherwise `promote --version vX.Y.Z` later finds no images and fails at the
copy step, a long way from the cause.

```bash
# Inspect: empty output for includedFiles is what you want on a tag trigger
gcloud beta builds triggers describe <trigger> \
  --project=<deploy-project> --region=<region> \
  --format="value(includedFiles,repositoryEventConfig.push.tag)"
```

**Re-firing after the fix:** the tag already exists, so nothing re-fires on its
own. Delete and re-push the same tag (`git push --delete origin vX.Y.Z &&
git push origin vX.Y.Z`) — that re-sends the webhook and proves the *automatic*
path works, which a manual `triggers run` would not.
