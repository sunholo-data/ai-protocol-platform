# Workshop quick-start

This guide gets you from `git clone` to a working chat UI in under 30 minutes,
with **zero GCP credentials required**.

> **Audience:** anyone running this codebase locally for the first time —
> workshop attendees, university students using the platform as a template,
> or developers exploring the protocol stack (ADK, AG-UI, A2UI, MCP, MCP Apps,
> A2A) without standing up cloud infrastructure first.

## TL;DR

```bash
git clone https://github.com/sunholo-data/ai-protocol-platform.git
cd platform
cp .env.example .env

# Edit .env and uncomment these two lines:
#   LOCAL_MODE=1
#   NEXT_PUBLIC_LOCAL_MODE=1

# In one terminal:
cd backend && make install && cd ..
make dev

# In another terminal:
cd frontend && npm install && npm run dev
```

Open <http://localhost:3456> — you should see a yellow LOCAL_MODE banner
across the top and a "Welcome to Aitana" page. Click a demo skill and
send a message. The agent streams a reply via AG-UI from a local ADK
runtime; no Firebase, no Firestore, no Vertex AI.

## What you get in LOCAL_MODE

Three demo skills, seeded into the in-memory Firestore at startup:

| Skill | Workshop module | What it demonstrates |
|---|---|---|
| **Demo Researcher** | W2 (ADK) | Plain ADK agent, AG-UI text streaming, no tools |
| **Demo Form Builder** | W6 (A2UI) | Emits A2UI form definitions the frontend renders |
| **Demo Map Explorer** | W7 (MCP Apps) | Placeholder — describes what cloud-mode would render |

The yellow banner at the top of every page reminds you what's stubbed
(Firestore, Firebase auth, Vertex AI Search, Cloud Trace, etc.). Data is
**in-memory and ephemeral** — it resets on every backend restart unless
you set `LOCAL_MODE_PERSIST=1` (writes to `~/.aitana-local/firestore.json`
on shutdown).

## What gets stubbed

| GCP service | LOCAL_MODE replacement |
|---|---|
| Firestore | `InMemoryFirestoreClient` (drop-in for the methods v6 uses) |
| Firebase Admin Auth | Stub dep that accepts only `local-mode-stub-token` |
| Firebase web auth | Frontend bypasses sign-in, uses a fixed workshop identity |
| ADK sessions | `InMemorySessionService` (resets on backend restart) |
| ADK artifacts | `InMemoryArtifactService` |
| Vertex AI Search | Tool returns a "disabled in LOCAL_MODE" message |
| Cloud Trace / Logging | No-op (logs still print to stdout) |
| MCP App sandbox | Falls back to "describe what I'd render" responses |

Everything else — ADK agent orchestration, AG-UI streaming, A2UI rendering,
the chat UI, document workspace, skills layer — runs identically to cloud
mode. The protocols don't care what backs the persistence layer.

## Tier 1 — LOCAL_MODE (you are here)

**Effort:** 0 minutes.
**What's real:** backend, frontend, agent, AG-UI, A2UI.
**What's stubbed:** Firestore, auth, GCS, Vertex Search, telemetry.

Already covered above. Move on when you want persistent data or
real Firebase auth.

## Tier 2 — Shared dev Firestore (5 min)

> Available when the workshop is being run by the Aitana Labs team. The
> shared `your-project-id` Firebase project has Anonymous Auth enabled
> (since 2026-05-02) with Firestore rules scoping each attendee's writes
> to their own anonymous UID.
>
> **Operator note** (Aitana Labs team only): the dev billing account
> already has an account-wide budget alert configured. If you fork this
> repo and want a project-scoped budget guard on your own shared tier,
> here's the canonical one-liner:
>
> ```bash
> gcloud billing budgets create \
>   --billing-account=YOUR-BILLING-ACCOUNT-ID \
>   --display-name="<your-project> workshop cap" \
>   --budget-amount=50 \
>   --threshold-rule=percent=0.5 \
>   --threshold-rule=percent=0.9 \
>   --filter-projects=projects/<your-project>
> ```

```bash
# In frontend/.env.local — paste from workshop materials:
NEXT_PUBLIC_FIREBASE_API_KEY=<from workshop>
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=your-project-id.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=your-project-id
NEXT_PUBLIC_FIREBASE_APP_ID=<from workshop>

# Do NOT set NEXT_PUBLIC_LOCAL_MODE (or set it to 0).
# Backend stays in LOCAL_MODE so no GCP creds are needed there.
```

Public Firebase web API keys are **not secrets** — security is enforced by
Firestore rules and auth-domain restrictions
([Firebase docs](https://firebase.google.com/docs/projects/api-keys)). The
budget is capped at $50 and rules deny cross-user reads.

## Tier 3 — Your own GCP project (30–60 min) {#graduating-from-local-mode}

For local development against your own Firestore, or when you want to deploy
the platform yourself.

### Step 1 — Create a GCP project

Enable these APIs in the [Cloud Console](https://console.cloud.google.com):

- Firestore (Native mode)
- Identity Platform (or Firebase Auth)
- Vertex AI
- Cloud Storage

### Step 2 — Application Default Credentials

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

The backend reads ADC for Firestore, Firebase Admin, and Vertex AI access.

### Step 3 — Firebase web app config

In the Firebase Console, **Project Settings → Your apps → Add app → Web**.
Copy the config snippet. Paste into `frontend/.env.local`:

```bash
NEXT_PUBLIC_FIREBASE_API_KEY=...
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=...
NEXT_PUBLIC_FIREBASE_PROJECT_ID=...
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=...
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=...
NEXT_PUBLIC_FIREBASE_APP_ID=...

# Unset (or set to 0):
NEXT_PUBLIC_LOCAL_MODE=0
```

### Step 4 — Backend env

In `backend/.env`:

```bash
GOOGLE_CLOUD_PROJECT=your-project-id
# Unset LOCAL_MODE
LOCAL_MODE=0
```

### Step 5 — Optional persistence beyond local Firestore

```bash
# Persistent chat history via Vertex AI Agent Engine
AGENT_ENGINE_ID=projects/YOUR_PROJECT/locations/YOUR_LOC/reasoningEngines/YOUR_ID
AGENT_ENGINE_STAGING_BUCKET=gs://your-staging-bucket

# GCS-backed artifacts (uploaded docs, intermediate tool outputs)
ADK_ARTIFACT_BUCKET=gs://your-artifact-bucket

# Vertex AI Search (the ai_search tool returns "disabled" if unset)
VERTEX_AI_SEARCH_DATASTORE_ID=projects/.../dataStores/...
```

> ⚠️ **Set both `AGENT_ENGINE_ID` and `ADK_ARTIFACT_BUCKET` together, or neither.**
> Mixing cloud sessions with in-memory artifacts strands sessions across
> backend restarts — the session's `docs_loaded` list survives but the
> artifacts behind it don't, and the document injector then loads nothing.
> The backend emits a `WARNING` at boot when only one is set.

### Step 6 — Restart

```bash
make dev
```

The yellow LOCAL_MODE banner disappears. Sign in with Google through Firebase.
Data persists in Firestore.

## Troubleshooting

**"Backend refuses to start: LOCAL_MODE=1 is set together with K_SERVICE..."**
You're trying to run LOCAL_MODE in a deployed context (Cloud Run / App Engine
/ GKE). This is a deliberate safety refusal — the auth-bypass stub must never
be active in production. Unset `LOCAL_MODE` for cloud deployments.

**Banner appears but chat returns 401**
The frontend is sending the stub token but the backend isn't recognising it.
Check that `LOCAL_MODE=1` is set in `backend/.env` and the backend has been
restarted since.

**Sign-in screen appears in LOCAL_MODE**
Frontend isn't reading `NEXT_PUBLIC_LOCAL_MODE`. Make sure it's in
`frontend/.env.local` (Next.js only reads `.env.local`, not `.env`) and
that you've restarted `npm run dev` since.

**"Search disabled in LOCAL_MODE" in chat replies**
Expected — Vertex AI Search isn't running locally. Set
`VERTEX_AI_SEARCH_DATASTORE_ID` in cloud mode if you need it.

## Architecture overview

For workshop attendees who want to read the code:

```
[Frontend :3456]
   ├─ Next.js 15 + React 19 + Tailwind
   ├─ AG-UI HttpAgent (streams SSE from backend)
   ├─ A2UI renderer for declarative UI from agents
   └─ MCP Apps iframe sandbox for protocol-prefixed widgets
       │
       └── /api/proxy/* → [Backend :1956]
                              ├─ FastAPI + Google ADK
                              ├─ Skills layer (3 demo skills in LOCAL_MODE)
                              ├─ ADK agent factory (Gemini / Claude / OpenAI)
                              ├─ MCP toolset for remote tools
                              └─ Auth: Firebase JWT or LOCAL_MODE stub
```

Read `docs/design/v6.1.0/SEQUENCE.md` for the build sequence and which
sprints shipped which protocol layer.

## Next steps

- Browse the demo skills' code at `backend/db/local_fixture.py`
- Read the protocol stack talk: `docs/talks/ai-ui-protocol-stack.md`
- Build your own skill: open the chat UI's **+ Create a new skill** button
- Contribute: see `CONTRIBUTING.md`
