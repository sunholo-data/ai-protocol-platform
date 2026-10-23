# Shared machine threads + AILANG messages tool

**Status:** ✅ Shipped (2026-10-01)
**Source:** Sunholo deployment — the operator wanted to review AND join the
conversations an agent (a tailnet machine principal) has on the platform.

## Problem

An agent using the platform through a machine token starts its own chat
threads. The operator could only see them in the admin analytics view (a
read-only trace) and could not say anything in them. Separately, the platform
had no way to send an AILANG message, which is how the operator's agents are
reached.

Two latent bugs surfaced on the way:

* The chat route only checked ownership when the body carried `sessionId`. The
  chat page reopens a thread with the AG-UI `threadId`, so a non-owner's
  message ran against someone else's thread id **as themselves**.
* `bootstrap` 403'd any non-owner, so a shared thread could not be opened.

## Design

**Supervisors on the principal.** A machine principal may name
`supervisors` (emails) and a `notifyInbox`:

```json
MACHINE_PRINCIPALS={"<sha256>": {"uid": "machine:agent", "displayName": "Agent",
  "supervisors": ["ada@example.com"], "notifyInbox": "agent"}}
```

Each new thread the machine starts is stamped `collaborators: [supervisors]`
on its `chat_sessions` row. `scripts/backfill_session_collaborators.py`
stamps threads started before a supervisor was added.

**Collaborators read and reply.** `AccessContext.is_collaborator` is true for
an email in `collaborators`, and `can_access` honours it, so every existing
read route (`GET /api/sessions/{id}`, `/messages`, `/activity`) works
unchanged. `GET /api/sessions?shared=true` lists them (index:
`collaborators CONTAINS, archivedAt, lastMessageAt desc`), each with
`owner_name` and `can_reply`. The UI shows them under **Shared with me** and
opens a thread on the skill it belongs to.

**A reply runs as the owner.** In `stream_skill`, a collaborator's turn is
executed with the OWNER's `User` and `AccessContext` — the owner's skills,
tools and session. This is the key decision: the owner reads the whole thread
afterwards, so the thread must never contain anything the owner could not have
fetched itself (e.g. a supervisor's billing data). The message is prefixed
`[Name]` so the transcript and the model both see who spoke, and
`turn_author` goes into session state for tools that report who they acted
for.

**The owner is pinged.** After a collaborator's turn the platform sends an
AILANG message to the owner's `notifyInbox` with the excerpt, the session id
and the two calls to read and continue it. Machines get `GET /api/sessions`
and `GET /api/sessions/*` in `DEFAULT_MACHINE_ROUTES` to read their threads
(a principal with its own `routes` must add them).

**Non-owners otherwise.** A non-owner, non-collaborator posting by `threadId`
now gets a visible 403 ("read-only — start a new chat"). The explicit
`sessionId` path keeps its existing `session_meta isReadOnly` behaviour.

## AILANG messages tool

`tools/ailang_messages.py`, tool name `ailang_messages` (catalog category
"Communication"). It posts to the AILANG coordinator's `POST /api/messages`
with a bearer key — the plane's public contract, not its storage.

* env `AILANG_COORDINATOR_URL`, `AILANG_COORDINATOR_API_KEY`
* Firestore `admin_config/ailang_messages`:
  `{"enabled": true, "from": "<sender>", "allowedInboxes": ["agent"]}` —
  missing or unreadable disables the tool (fail closed). A model can only
  reach an allowlisted inbox; every message says whom it was sent for.
* `notify()` is the platform's own sender (the shared-thread ping). Its inbox
  comes from operator config, so the allowlist does not apply. It never raises.

## Out of scope

* Sharing human-owned threads with other humans (the field is generic; only
  machine principals set it today).
* Live push of a supervisor's reply into a machine's open stream — the machine
  reads it after the ping.
