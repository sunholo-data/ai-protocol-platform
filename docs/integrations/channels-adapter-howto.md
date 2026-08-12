# Channel Adapter Howto

**Audience:** anyone adding a new messaging channel (Slack, Twilio, custom HTTP webhook, IRC, …) to the Aitana v6 platform.

**Goal:** ship a working adapter in ~4 hours by subclassing `BaseChannel`, implementing three methods, and registering once.

**Worked example:** `backend/channels/_demo_cli.py` — a stdin/stdout demo channel built by following this doc top-to-bottom. The demo is internal-only (never registered in `fast_api_app.py`), but the implementation is the canonical "minimal valid adapter".

> **Deeper reference:** the [channels design doc](../design/v6.1.0/channels.md) covers the framework rationale, security model, and the v5→v6 migration. This howto is the operating manual.

---

## TL;DR

```python
# backend/channels/myservice.py — ~80 LOC adapter
from channels.base import BaseChannel, InboundMessage, OutboundMessage


class MyServiceChannel(BaseChannel):
    name = "myservice"
    command_prefix = "/"

    async def verify_webhook(self, headers, body):
        # check signature header against MYSERVICE_SIGNING_KEY
        ...

    async def parse_inbound(self, payload):
        # normalise channel-native payload to InboundMessage
        # return None for non-actionable events (typing pings, ack receipts)
        ...

    async def send(self, chat_id, message):
        # POST message.text to the channel's outbound API
        ...
```

Then in `fast_api_app.py`:

```python
if os.getenv("MYSERVICE_SIGNING_KEY"):
    ChannelRegistry.register(MyServiceChannel())
# ChannelRegistry.mount_webhooks(app) is already called once at the bottom.
```

That's it. The framework auto-mounts `POST /api/myservice/webhook` and runs the full
verify → parse → identity → command → skill → send flow for you.

---

## 1. The 3 abstract methods you MUST implement

Every adapter subclasses `BaseChannel` (see `backend/channels/base.py`) and implements these three. They are the ABC's `@abstractmethod` set — Python refuses to instantiate the class without them.

### 1.1 `verify_webhook(headers, body) -> bool`

**Contract:** return `True` if the request demonstrably came from the channel provider, `False` otherwise. Never raise — the framework converts `False` into a clean `401`.

**Minimal valid implementation (CLI demo):**

```python
async def verify_webhook(self, headers, body):
    # CLI has no transport-layer auth — the process owner is trusted.
    return True
```

The CLI demo can do this safely because it is not exposed over HTTP. **Any real channel must verify.** Look at:

- `channels/email_.py::verify_webhook` — Mailgun HMAC-SHA256 over `timestamp + token`
- `channels/discord.py::verify_webhook` — Ed25519 over `timestamp + body` (NaCl)

**Fail-closed is the v6 default.** If the signing key isn't configured, return `False`. The Email adapter explicitly logs + rejects when `MAILGUN_SIGNING_KEY` is empty (v5 logged a warning and accepted — don't copy that).

### 1.2 `parse_inbound(payload) -> InboundMessage | None`

**Contract:** map the channel-native webhook payload into a normalised `InboundMessage`. Return `None` to mark the event non-actionable (typing indicators, delivery receipts, message-edited acks). The framework responds with `{"ok": true, "skipped": true}` on `None`.

**Minimal valid implementation (CLI demo):**

```python
async def parse_inbound(self, payload):
    text = (payload.get("text") or "").strip()
    if not text:
        return None
    return InboundMessage(
        channel_user_id=str(payload.get("user_id") or "cli-user"),
        channel_chat_id="stdout",
        text=text,
        metadata={"transport": "cli"},
    )
```

The five `InboundMessage` fields:

| Field | What it is |
|---|---|
| `channel_user_id` | Native user ID (Telegram user ID, email address, Discord snowflake). The framework uses this to look up a Firebase UID. |
| `channel_chat_id` | Where the reply goes. The framework passes this to your `send()`. For email it's the inbound's sender address. For Discord it's the channel/thread ID. |
| `text` | The user's message. Prefix-strip already done if your channel does mentions (Discord strips the `@bot ` prefix). |
| `attachments` | List of `Attachment` objects. The framework's `AttachmentPipeline` downloads + uploads + registers them as documents. |
| `metadata` | Channel-specific fields you want to flow through to `send()` (subject line, in-reply-to header, guild ID, …). The framework forwards this into the eventual `OutboundMessage.metadata`. See §5. |

### 1.3 `send(chat_id, message) -> None`

**Contract:** deliver `message.text` to the channel API at `chat_id`. Handle channel-specific length limits internally.

**Minimal valid implementation (CLI demo):**

```python
async def send(self, chat_id, message):
    sys.stdout.write(f"\n[skill] {message.text}\n\n")
    sys.stdout.flush()
```

Real channels look like:

- `email_.py::send` — `requests.post` to `/v3/{domain}/messages`, with `h:In-Reply-To` from `message.metadata`.
- `discord.py::send` — chunks `message.text` at 2000 chars (`chunk_message` helper), then `await channel.send(chunk)` per chunk.

**Length limits live here.** Telegram 4096, Discord 2000, WhatsApp 1024 chars — the framework does not chunk for you. Pull `channels/_chunk.py::chunk_message` if you need a generic splitter.

---

## 2. The 2 methods you MAY override

Both have sensible defaults in `BaseChannel`. Override only when your channel has a richer routing or onboarding model.

### 2.1 `select_skill(msg, firebase_uid) -> str | None`

**Default:** look up the user's stored default skill from `user_settings/{firebase_uid}.default_skill_id`.

**Override when:** your channel can route messages to different skills based on something other than the user's default. The email adapter is the canonical example — the `[Skill Name]` prefix in the email subject overrides the default:

```python
# channels/email_.py
async def select_skill(self, msg, firebase_uid):
    subject = msg.metadata.get("subject") or ""
    cmd = CommandParser.parse(subject, prefix=self.command_prefix)
    if cmd is not None and cmd.name == "skill" and cmd.args:
        resolved = await _resolve_skill_by_name_or_id(cmd.args[0])
        if resolved is not None:
            return resolved
    return await super().select_skill(msg, firebase_uid)
```

Return `None` to fall back to `general-assistant` (the framework handles that fallback).

### 2.2 `on_unknown_user(msg) -> str | None`

**Default:** auto-create a `channel_identities` mapping with a fresh Firebase UID derived from the channel user ID. Anyone who messages the bot becomes a v6 user.

**Override when:** you need gated onboarding. Discord's allowlist is the canonical example:

```python
# channels/discord.py
async def on_unknown_user(self, msg):
    guild_id = msg.metadata.get("guild_id")
    if not guild_id:  # DMs are admin-only
        return None
    route = get_document("channel_routes", f"discord_{guild_id}")
    if not route or msg.channel_user_id not in (route.get("allowed_user_ids") or []):
        return None  # → framework returns {"ok": True, "rejected": "unknown_user"}
    return await IdentityResolver.auto_create(self.name, msg.channel_user_id)
```

Return `None` to reject — the framework responds with `{"ok": true, "rejected": "unknown_user"}` and never invokes a skill.

---

## 3. Registering the channel

Adapters do **not** touch FastAPI routing. The `ChannelRegistry` mounts `POST /api/{name}/webhook` for every registered channel in one shot.

**Wire in `backend/fast_api_app.py`** (env-var gated so local dev + LOCAL_MODE still boot without creds):

```python
from channels.myservice import MyServiceChannel

_myservice_key = os.getenv("MYSERVICE_SIGNING_KEY", "")
if _myservice_key:
    ChannelRegistry.register(MyServiceChannel(signing_key=_myservice_key))
else:
    _log.info("myservice channel not registered: MYSERVICE_SIGNING_KEY not set")

# `ChannelRegistry.mount_webhooks(app)` is already called once at the
# bottom of fast_api_app.py — do not add a second call. Idempotent on
# re-registration of the SAME instance; raises on a DIFFERENT instance.
```

The env-var gate is non-negotiable. If your adapter raises on missing creds at construction time, `LOCAL_MODE=1` boots break for every dev who doesn't have the channel's secrets. Gate at the registration site, not inside the constructor.

---

## 4. Non-webhook transports (gateway / WebSocket / polling)

Some channels don't push webhooks — Discord uses a persistent gateway WebSocket for mentions and DMs (only slash-commands are webhook-delivered). Slack RTM, IRC, and Matrix work the same way.

For these paths, the framework still gives you `BaseChannel._dispatch_inbound(inbound)`. That helper runs the **post-parse** flow — identity → command → attachments → skill → send — without re-running `verify_webhook` or `parse_inbound`. The gateway's TLS auth + the adapter's parsing of the native message object replace those two steps.

**Discord's gateway handler:**

```python
# channels/discord.py — abridged
async def on_message(self, message):
    # Adapter-specific filters (only react to DMs + mentions)
    if not (is_dm(message) or is_mention(message, self.client.user)):
        return

    inbound = InboundMessage(
        channel_user_id=str(message.author.id),
        channel_chat_id=str(message.channel.id),
        text=message.content.replace(self.client.user.mention, "").strip(),
        metadata={"guild_id": str(message.guild.id) if message.guild else None},
    )
    await self._dispatch_inbound(inbound)  # ← re-enter the framework
```

`_dispatch_inbound` is the single source of truth for what happens to an `InboundMessage`. If you ever find yourself reimplementing identity resolution or command parsing in your adapter — stop, you should be calling `_dispatch_inbound`.

---

## 5. Reply threading + metadata forward

The framework forwards `inbound.metadata` into the `OutboundMessage.metadata` it constructs before calling your `send()`. This is how reply-threading semantics work without per-channel plumbing in the framework.

```
parse_inbound → InboundMessage(metadata={"in_reply_to": "<msg-id>", ...})
                                  │
                                  ▼ (framework, base.py::_dispatch_inbound)
        OutboundMessage(text=reply, metadata=inbound.metadata)
                                  │
                                  ▼
                              send(chat_id, message)
                              # message.metadata["in_reply_to"] is yours to use
```

**Email puts the SMTP threading headers there:**

```python
# parse_inbound:
return InboundMessage(
    channel_user_id=sender,
    channel_chat_id=sender,  # for email, chat_id is "where the reply goes"
    text=body_plain,
    metadata={
        "subject": subject,
        "in_reply_to": message_id,
        "references": references,
        "to": sender,
    },
)

# send:
async def send(self, chat_id, message):
    in_reply_to = message.metadata.get("in_reply_to") or ""
    data = {"from": ..., "to": chat_id, "subject": f"Re: {message.metadata.get('subject', '')}", "text": message.text}
    if in_reply_to:
        data["h:In-Reply-To"] = in_reply_to
        data["h:References"] = in_reply_to
    requests.post(...)
```

The metadata forward also flows on the command path (`/skill foo` replies thread the same way), so users who switch skills mid-thread stay in their thread.

---

## 6. Tests you should write

Mirror the structure in `backend/tests/channels/test_email.py` and `test_discord.py`:

1. **`TestVerifyWebhook`** — valid signature accepted; missing / wrong / tampered → False. Always include a `test_no_signing_key_rejects_for_safety` case.
2. **`TestParseInbound`** — typical actionable payload, non-actionable variants → `None`, missing-required-field variants → `None` with a log.
3. **`TestSend`** — happy path mocks the channel's HTTP client / SDK and asserts the call args; chunk / length-limit cases if the channel has one.
4. **`TestSelectSkill` / `TestOnUnknownUser`** — only if you overrode them.

**Auto-enroll in the cross-channel smoke contract test.** Add a `_build_myservice()` entry to `backend/tests/channels/test_smoke_all_channels.py::_ALL_CHANNELS` and the same three contracts (valid → 200, bad signature → 401, non-actionable → skipped) get checked for free.

---

## 7. The 4h budget

This is the breakdown that "new channel in ~4h" actually means.

| Phase | Time | What you do |
|---|---|---|
| Read the framework | 20 min | `channels/base.py` + one existing adapter (Discord or Email) end-to-end |
| Capture a real payload | 30 min | Tap a single webhook delivery from the channel's dev console; save to `tests/channels/fixtures/<channel>_inbound.json` |
| `verify_webhook` + tests | 30 min | Signature scheme is ~10 lines; tests are the structure above |
| `parse_inbound` + tests | 45 min | Map payload fields; the captured fixture drives the tests |
| `send` + tests | 45 min | One HTTP POST or SDK call; chunk if length-limited |
| Optional overrides | 30 min | `select_skill` / `on_unknown_user` only if needed |
| Register + env-var gate | 10 min | One block in `fast_api_app.py` |
| Cross-channel smoke + sprint plan update | 20 min | Add to `_ALL_CHANNELS`; bump milestone JSON |

Total: ~3h45m for a straightforward channel; ~4h with streaming or gateway transport. The framework absorbs ~80% of the work a v5-style adapter would do.

---

## 8. Common pitfalls

### Pre-push CI parity

The repo's CI runs **both** `ruff check` and `ruff format --check`, then pytest. Running `make test-fast` alone misses the formatter check. Always run the parity combo before pushing:

```bash
cd backend && make lint && make test-fast
```

`make lint` does NOT run pytest. `make test-fast` does NOT run the formatter. Both are required. See [the pre-push gotcha in CLAUDE.md](../../CLAUDE.md) — the LOCAL-MODE-AND-FORK sprint shipped 9 dev commits before noticing CI was red because it relied on the fast variants.

### Firestore rules for new collections

If your adapter writes to a new Firestore collection (a channel-specific allowlist, a per-thread state doc), update `firestore.rules` AND deploy:

```bash
firebase deploy --only firestore:rules --project=your-project-id
```

The deploy step does not run from the CI pipeline — it's a manual gate per env. Don't ship the adapter without the rule, or local dev with emulator-mode auth will silently allow what production rejects.

### Env-var gating to keep LOCAL_MODE booting

Wrap `ChannelRegistry.register(...)` in an `if os.getenv("...")` check. If your channel's constructor fails on missing creds, every fresh-clone dev whose `.env` is empty gets a broken `make dev`. The Email and Discord adapters both follow this pattern — copy it.

### Don't bypass `_dispatch_inbound`

Adapters MUST NOT call `process_skill_request` directly. The framework is the only integration point between channels and the skill system — that's how the audit log, identity resolution, and command dispatch stay uniform. If a gateway transport seems to need a direct call, you actually want `await self._dispatch_inbound(inbound)`.

### `name` is a class variable

`class MyChannel(BaseChannel)` needs `name: ClassVar[str] = "myservice"` at the class level, not in `__init__`. The framework reads it via `type(self).name` at registration time. Forgetting this raises `ValueError("MyChannel must set the \`name\` class attribute")` on first `register()`.

### The CLI demo is NOT a production channel

`backend/channels/_demo_cli.py` is internal-only. It exists so this howto has a worked example you can read alongside the doc. Do not register it in `fast_api_app.py`. Its `verify_webhook` returns `True` unconditionally — that's safe for stdin but catastrophic for HTTP.

---

## See also

- [Channels design doc](../design/v6.1.0/channels.md) — framework architecture, security model, v5→v6 migration map
- [`backend/channels/base.py`](../../backend/channels/base.py) — the ABC source-of-truth; every contract above is enforced or documented in this file
- [`backend/channels/_demo_cli.py`](../../backend/channels/_demo_cli.py) — the worked example
- [`backend/tests/channels/test_smoke_all_channels.py`](../../backend/tests/channels/test_smoke_all_channels.py) — the contract test new channels enrol in
- [`scripts/smoke-deployed.sh`](../../scripts/smoke-deployed.sh) `channels` target — deployment reachability probe (reads OpenAPI tags)
