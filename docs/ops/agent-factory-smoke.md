# Agent factory smoke — `/api/skill/{skill_id}/stream`

Verifies the AGENT-FACTORY sprint output end-to-end on a deployed Cloud Run
service: FastAPI receives an authenticated POST, the skill processor looks
up the skill in Firestore, the ADK agent factory builds a per-user
`LlmAgent`, `ag_ui_adk.ADKAgent` translates ADK events to AG-UI events,
and the response streams back as Server-Sent Events.

**Runs against:** `platform-backend` on `your-project-id`
(see [deployed-urls.md](deployed-urls.md) for URL resolution).

## What this probes

```
 client POST /api/skill/<skill_id>/stream
   → Firebase ID token verified (Depends(get_current_user))
   → AccessContext built from custom claims + skill accessControl
   → get_skill(skill_id) from Firestore
   → create_agent_with_thinking(skill, user) — LlmAgent with planner
   → ADKAgent.run(RunAgentInput) yields AG-UI events
   → StreamingResponse writes "data: <json>\n\n" per event
```

A green run emits, at minimum: `RUN_STARTED`, `TEXT_MESSAGE_START`,
one or more `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `RUN_FINISHED`.

## Pre-reqs (one-time, per environment)

- Firebase email/password sign-in enabled on the project
  (see [dev-accounts.md](dev-accounts.md)).
- At least one public skill seeded in Firestore — run
  `cd backend && uv run python scripts/seed_skills.py --owner-uid <uid>`
  if the collection is empty.
- `gcloud auth application-default login` locally (needed to rotate the
  smoke user's password via the admin SDK).

## Recipe

```bash
# --- 1. Mint a fresh Firebase ID token for the smoke-test user ---
#     Reuses the whoami smoke's password-rotation helper. The token is
#     valid for ~1 hour; copy it into the curl below.
cd backend
uv run python -c "
import secrets
from firebase_admin import auth, initialize_app
from scripts.whoami_smoke import _ensure_user, _sign_in
from scripts._env import ENVIRONMENTS
cfg = ENVIRONMENTS['dev']
initialize_app(options={'projectId': cfg['project_id']})
pw = secrets.token_urlsafe(24)
_ensure_user(pw)
print(_sign_in(cfg['api_key'], pw))
" > /tmp/idtoken.txt
ID_TOKEN=$(cat /tmp/idtoken.txt)

# --- 2. Find a public skill ID in Firestore ---
#     Any seeded skill with accessControl.type == 'public' works. Pick
#     the first one by name via gcloud firestore query:
SKILL_ID=$(gcloud firestore documents list --collection-id=skills \
  --project=your-project-id --format='value(name)' --limit=1 \
  | awk -F/ '{print $NF}')
echo "SKILL_ID=$SKILL_ID"

# --- 3. Resolve the backend URL (IAM-protected service) ---
BACKEND_URL=$(gcloud run services describe platform-backend \
  --project=your-project-id --region=europe-west1 \
  --format='value(status.url)')

# --- 4. Curl the SSE endpoint via the frontend proxy ---
#     The frontend handles /api/proxy/* forwarding + Bearer pass-through.
#     Using the proxy means the test exercises the same path real clients
#     take, including the sidecar port (localhost:1956).
FRONTEND_URL="https://your-service-url.example"
curl -N -sS \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Say hello in five words."}' \
  "$FRONTEND_URL/api/proxy/api/skill/$SKILL_ID/stream"
```

## Expected output

A stream of `data:` lines, each carrying one AG-UI event:

```
data: {"type":"RUN_STARTED","threadId":"thread-abc123...","runId":"run-def456"}

data: {"type":"TEXT_MESSAGE_START","messageId":"m1","role":"assistant"}

data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"Hello "}

data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"from Gemini."}

...

data: {"type":"TEXT_MESSAGE_END","messageId":"m1"}

data: {"type":"RUN_FINISHED","threadId":"thread-abc123...","runId":"run-def456"}
```

The `RUN_STARTED` and `RUN_FINISHED` events book-end the turn. Between
them, every `TEXT_MESSAGE_CONTENT.delta` concatenates into the model's
response. `TEXT_MESSAGE_START/END` bracket each assistant message.

## Failure modes

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` | ID token expired (>1 hr), or `Authorization` header stripped by the proxy |
| `404 Skill not found` | Wrong `$SKILL_ID`, skill deleted, or skill not visible to the smoke user (check `accessControl.type`) |
| Stream opens, then closes after `RUN_STARTED` with no content | Vertex / Gemini credentials missing on the backend service. Check `gcloud run services describe platform-backend` for `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` env vars |
| `500 Internal Server Error` with `Sub-skill cycle detected` | Seeded skill references itself via `skillMetadata.sub_skills`. Should never happen from `seed_skills.py`; inspect Firestore doc directly |
| Half-open stream (no `data:` frames, curl hangs) | Sidecar / ingress port drift on the frontend multi-container. See [incidents/fe-bringup-1-proxy-404.md](incidents/fe-bringup-1-proxy-404.md) |
| `Bad Request` on construct | `RunAgentInput` expects camelCase on input, snake_case on read — if you see attribute errors in logs, that's the one to check first |

## Related

- Design: [agent-factory.md](../design/v6.0.0/agent-factory.md)
- Sprint plan: [agent-factory-sprint.md](../design/v6.0.0/agent-factory-sprint.md)
- Sprint JSON: `.claude/state/sprints/sprint_AGENT-FACTORY.json`
- Integration test (mocked LLM): `backend/tests/api_tests/test_stream_skill.py`
- Auth chain: [auth-smoke-testing.md](auth-smoke-testing.md)
