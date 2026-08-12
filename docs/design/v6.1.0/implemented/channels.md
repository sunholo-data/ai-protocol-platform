# Channels

**Status**: ✅ Implemented 2026-05-16 — all 5 milestones shipped (M1 framework + M2 Discord + M3 Email + M4 Telegram+WhatsApp + M5 smoke+howto+CLI demo)
**Priority**: P1 (Medium) — Shepherd / an internal-tools fork no longer blocked
**Estimated**: 3.5 days
**Actual**: 1 calendar day via parallel Task sub-agents (M2+M3, then M4+M5)
**Scope**: Backend channel framework + 4 production adapters + 1 demo adapter + Cloud Run TF module + adapter howto
**Dependencies**: [Agent Factory](agent-factory.md), [Auth & Permissions](auth-and-permissions.md)
**Created**: 2026-04-10
**Last Updated**: 2026-05-16 — sprint complete. Net: 196 new tests (871 → 1067 backend), 153 channel tests, `make lint` clean, all evaluator rounds PASS (M1=95, M2=92, M3=94, M4=90, M5=96 / 100). Framework gaps surfaced by M2/M3 round-1 evaluations were closed in commit 6c55b43 (`BaseChannel._dispatch_inbound` shared helper + `inbound.metadata` → `OutboundMessage.metadata` forward). Operating manual at [docs/integrations/channels-adapter-howto.md](../../integrations/channels-adapter-howto.md). See [channels-sprint.md](channels-sprint.md) for full milestone breakdown.

## Problem Statement

v5 supports three messaging channels (Telegram, Email, WhatsApp) that each independently route through `process_assistant_request()`. The v5 implementations are working code but they're **three loosely-coupled modules with copy-paste boilerplate** — identity resolution, command parsing, attachment handling, message splitting are all repeated per channel. Adding a fourth channel (Discord, demanded by the [an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md)) under that pattern means copying 80% again.

v6 has the chance to fix this once. The right shape is a `BaseChannel` framework plus thin per-channel adapters, where adding a new channel is "subclass + 3 methods + ~80 LOC."

Key questions:
- How does a Telegram/Discord user select which skill to use?
- How do channels handle skill-specific features (A2UI, MCP Apps) that only work on web?
- Should channels share sessions with web (same conversation across channels)?
- **Can a new channel adapter be written in <4h once the framework lands?**

**Current State:**
- `backend/channels/` has empty `__init__.py` files for telegram, email, whatsapp
- No framework abstraction — each channel would be self-contained as designed in v5
- v5 has working implementations: `telegram_service.py`, `email_integration.py`, `whatsapp_service.py`, each ~300-500 LOC with significant overlap
- Webhook URLs are preserved (same Cloud Run services, same paths) — framework must respect this
- Discord scaffold available at `<local-path>` (different transport entirely — discord.py + gateway, not webhook)

**Impact:**
- Required for v5 feature parity (Telegram + email + WhatsApp)
- Required for [an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md) (Discord)
- Required for [event-driven skills](../v6.2.0/event-driven-skills.md) — trigger output routing depends on `Channel.send()` being uniform
- Discord is now first-batch priority over Telegram (Shepherd ships before any v6 Telegram user)

## Goals

**Primary Goal:** Ship a `BaseChannel` framework and four channel adapters (Discord, email, Telegram, WhatsApp) that route through `process_skill_request()`, where adding a fifth channel is a ~4h job.

**Success Metrics:**
- Framework lands first; per-channel adapter is ≤120 LOC of channel-API specifics
- Discord, email, Telegram, WhatsApp all in production by end of sprint
- Adding a new channel (Slack, MS Teams, signal, etc.) is documented and demonstrably ~4h start-to-merge
- All existing v5 webhook URLs work without reconfiguration
- Skill selection works per channel with shared command parser (`/skill`, `/skills`, `/help`, `/clear`)
- [Event-driven skills](../v6.2.0/event-driven-skills.md) can route output to any registered channel via `ChannelRegistry.get(name).send()`

**Non-Goals:**
- Rich UI in channels (A2UI/MCP Apps are web-only)
- Voice in Telegram (use web for Gemini Live)
- Streaming responses to channels (collect-then-send remains, channels don't support partial-edit well outside Discord)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Channels collect full response before sending (non-streaming); Discord can edit live but framework defaults to atomic send |
| 2 | EARNED TRUST | 0 | Channels pass through agent responses unchanged |
| 3 | SKILLS, NOT FEATURES | +2 | **BaseChannel enforces** every channel speaks skills uniformly — shared command parser, shared skill-selection contract |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Delegates to agent factory |
| 5 | GRACEFUL DEGRADATION | +1 | Text-only rendering — graceful fallback for A2UI/MCP content |
| 6 | PROTOCOL OVER CUSTOM | +1 | **The channel framework IS the protocol** between transport adapters and the skill system — not just consumers of external APIs |
| 7 | API FIRST | +2 | **Framework enforces** the API boundary — channels physically cannot bypass `process_skill_request()` |
| 8 | OBSERVABLE BY DEFAULT | +1 | Channel name as event-level metadata in audit log + tracing standardised across adapters |
| 9 | SECURE BY CONSTRUCTION | +1 | Webhook verification, Firebase UID mapping, same permission model — enforced by `BaseChannel.handle_webhook` |
| 10 | THIN CLIENT, FAT PROTOCOL | +2 | **Framework enforces** — channel = adapter, period. No business logic possible in subclasses |
| | **Net Score** | **+10** | Threshold: >= +4. Framework refactor doubled the score |

## Design

> **See also:** [channels-adapter-howto.md](../../integrations/channels-adapter-howto.md) — the operating manual for *adding* a new channel adapter, with `backend/channels/_demo_cli.py` as the worked example. This design doc covers the framework's rationale; the howto covers the procedure.

### Overview

Channels are split into a **framework layer** (shared abstraction, mounted once) and **adapter layer** (one thin module per channel). The framework owns identity resolution, command parsing, attachment handling, skill routing, webhook verification scaffolding, and registry-based auto-mounting. An adapter implements only the channel-specific bits: parse the incoming wire format, send an outbound message, verify the transport's signature.

A new channel = subclass `BaseChannel`, implement 3 abstract methods, register at app start. ~80-120 LOC including imports.

### Channel Architecture

```
[External Service: Telegram / Discord / Mailgun / Twilio]
    │ webhook (or gateway for Discord)
    ▼
[Next.js API Route]  ← /api/{channel}/webhook (proxied)
    │
    ▼
[ChannelRegistry-mounted FastAPI route]
    │
    ▼
[BaseChannel.handle_webhook]  ← framework provides this
    ├── verify_webhook()         ← adapter implements
    ├── parse_inbound()           ← adapter implements
    ├── IdentityResolver.resolve()  ← framework
    ├── CommandParser.parse()       ← framework (handles /skill, /skills, /help)
    ├── select_skill()              ← framework default; adapter may override
    ├── AttachmentPipeline.upload() ← framework
    ├── process_skill_request()    ← shared with web
    └── send()                     ← adapter implements
```

### Channel Framework

The framework lives in `backend/channels/`:

```
backend/channels/
  base.py          # BaseChannel ABC + InboundMessage/OutboundMessage models
  registry.py      # ChannelRegistry — register + auto-mount webhooks
  commands.py      # CommandParser — /skill, /skills, /help, /clear; channel-agnostic
  attachments.py   # AttachmentPipeline — size guard, GCS upload, AILANG Parse, artifact
  identity.py      # IdentityResolver — channel_user_id → Firebase UID via Firestore
  discord.py       # adapter (Phase 1)
  email.py         # adapter (Phase 1)
  telegram.py      # adapter (Phase 2)
  whatsapp.py      # adapter (Phase 2)
```

#### `BaseChannel` ABC

```python
class InboundMessage(BaseModel):
    channel_user_id: str            # Telegram user ID, email address, Discord user ID
    channel_chat_id: str             # Telegram chat ID, email thread, Discord channel+thread
    text: str
    attachments: list[Attachment] = []
    raw: dict                        # original webhook payload for debugging
    metadata: dict = {}              # channel-specific (e.g., discord guild_id)

class OutboundMessage(BaseModel):
    text: str
    format: Literal["plain", "html", "markdown"] = "plain"
    attachments: list[Attachment] = []

class BaseChannel(ABC):
    name: ClassVar[str]              # registry key: "telegram", "discord", "email"
    command_prefix: ClassVar[str] = "/"  # email overrides to "[" for [SkillName] subject pattern

    # --- adapter MUST implement ---
    @abstractmethod
    async def verify_webhook(self, headers: dict, body: bytes) -> bool: ...

    @abstractmethod
    async def parse_inbound(self, payload: dict) -> InboundMessage | None: ...

    @abstractmethod
    async def send(self, chat_id: str, message: OutboundMessage) -> None: ...

    # --- adapter MAY override (defaults provided) ---
    async def select_skill(self, msg: InboundMessage, firebase_uid: str) -> str:
        """Default: user's stored default skill. Email overrides to extract from subject prefix."""
        return await get_user_default_skill(firebase_uid)

    async def on_unknown_user(self, msg: InboundMessage) -> str | None:
        """Default: auto-create channel_identity mapping. Override to require allowlist (e.g., Discord)."""
        ...

    # --- framework provides; subclasses do not touch ---
    async def handle_webhook(self, payload: dict, headers: dict, body: bytes) -> dict:
        if not await self.verify_webhook(headers, body):
            raise HTTPException(401, "webhook verification failed")

        inbound = await self.parse_inbound(payload)
        if inbound is None:
            return {"ok": True, "skipped": True}

        firebase_uid = await IdentityResolver.resolve(self.name, inbound.channel_user_id)
        if firebase_uid is None:
            firebase_uid = await self.on_unknown_user(inbound)
            if firebase_uid is None:
                return {"ok": True, "rejected": "unknown_user"}

        if cmd := CommandParser.parse(inbound.text, prefix=self.command_prefix):
            await self._handle_command(cmd, firebase_uid, inbound)
            return {"ok": True, "command": cmd.name}

        artifact_ids = await AttachmentPipeline.upload(inbound.attachments, firebase_uid)

        skill_id = await self.select_skill(inbound, firebase_uid)
        response = await process_skill_request(
            skill_id=skill_id,
            user_id=firebase_uid,
            message=inbound.text,
            attachment_ids=artifact_ids,
            channel=self.name,                  # event-level metadata for audit log
            channel_metadata=inbound.metadata,
        )
        await self.send(inbound.channel_chat_id, OutboundMessage(text=response))
        return {"ok": True}
```

#### `ChannelRegistry`

Channels self-register at app start; the registry auto-mounts webhook endpoints. Wire it once in `fast_api_app.py`:

```python
# backend/fast_api_app.py
from backend.channels import discord, email_, telegram_, whatsapp
from backend.channels.registry import ChannelRegistry

ChannelRegistry.register(discord.DiscordChannel())
ChannelRegistry.register(email_.EmailChannel())
ChannelRegistry.register(telegram_.TelegramChannel())
ChannelRegistry.register(whatsapp.WhatsAppChannel())
ChannelRegistry.mount_webhooks(app)   # mounts POST /api/{name}/webhook for each
```

```python
# backend/channels/registry.py
class ChannelRegistry:
    _channels: dict[str, BaseChannel] = {}

    @classmethod
    def register(cls, channel: BaseChannel) -> None:
        cls._channels[channel.name] = channel

    @classmethod
    def get(cls, name: str) -> BaseChannel:
        return cls._channels[name]

    @classmethod
    def mount_webhooks(cls, app: FastAPI) -> None:
        for name, channel in cls._channels.items():
            cls._mount_one(app, name, channel)

    @classmethod
    def _mount_one(cls, app: FastAPI, name: str, channel: BaseChannel) -> None:
        @app.post(f"/api/{name}/webhook")
        async def webhook(request: Request):
            body = await request.body()
            payload = await request.json()
            return await channel.handle_webhook(payload, dict(request.headers), body)
```

#### `CommandParser`

Single parser, channel-agnostic. Channels declare their prefix; the parser returns a structured `Command(name, args)` or `None`.

Initial commands:
- `/skill <name>` — switch user's default skill
- `/skills` — list available skills
- `/help` — channel-specific help text
- `/clear` — reset the current session

Email overrides `command_prefix = "["`, so `[Document Analyst] please summarise` is parsed as `Command(name="skill", args=["Document Analyst"])` with the remaining body as the message.

#### `AttachmentPipeline`

Shared upload path: size guard (1MB default per attachment, channel-configurable), GCS upload to `gs://<artifacts-bucket>/users/{uid}/{uuid}`, AILANG Parse for `.pdf`/`.docx`/`.pptx`/etc., artifact registration. Returns artifact IDs for the skill request.

#### `IdentityResolver`

Firestore-backed `channel_identities/{channel}_{channel_user_id}` collection maps to Firebase UID. On miss, `BaseChannel.on_unknown_user()` decides — default is auto-create (Telegram, email), override to require allowlist (Discord guild member check).

#### Adding a new channel

```python
# backend/channels/slack.py — illustrative
class SlackChannel(BaseChannel):
    name = "slack"

    async def verify_webhook(self, headers, body):
        return verify_slack_signature(headers, body, SLACK_SIGNING_SECRET)

    async def parse_inbound(self, payload):
        if payload.get("type") != "event_callback":
            return None
        ev = payload["event"]
        return InboundMessage(
            channel_user_id=ev["user"],
            channel_chat_id=ev["channel"],
            text=ev["text"],
            raw=payload,
            metadata={"team_id": payload["team_id"]},
        )

    async def send(self, chat_id, message):
        await slack_client.chat_postMessage(channel=chat_id, text=message.text)
```

~30 LOC plus the slack-sdk import. Webhook auto-mounts at `/api/slack/webhook`.

### Skill Selection Per Channel

Default `BaseChannel.select_skill()` returns the user's stored default. Channels override this when they have a richer skill-signalling convention.

**Discord** — default skill per user; `/skill <name>` slash command to switch; per-guild default fallback (Firestore `channel_routes/discord/{guild_id}` → `skill_slug`).

**Email** — overrides `select_skill()` to extract from subject prefix `[SkillName] body...`; the `CommandParser` with `command_prefix="["` handles this uniformly. Falls back to per-address routing (`skill-name@the internal-tools fork.org`).

**Telegram** — default skill per user via `user:default_skill` session state; `/skill <name>` to switch.

**WhatsApp** — same as Telegram.

If no default and no explicit selection: route to "General Assistant" template skill.

### Adapter sketches

Each adapter is ~80-120 LOC. Full implementations live in their files; the design here shows the surface area.

#### Discord (Phase 1)

```python
# backend/channels/discord.py
class DiscordChannel(BaseChannel):
    name = "discord"

    def __init__(self):
        self.client = discord.Client(intents=...)  # See discord-channel.md for full setup

    async def verify_webhook(self, headers, body):
        # Discord uses Ed25519 signature on interactions; gateway is separate
        return verify_discord_signature(headers, body, DISCORD_PUBLIC_KEY)

    async def parse_inbound(self, payload):
        # Discord slash command or message interaction
        return InboundMessage(
            channel_user_id=str(payload["member"]["user"]["id"]),
            channel_chat_id=str(payload["channel_id"]),
            text=payload["data"]["options"][0]["value"],
            raw=payload,
            metadata={"guild_id": payload["guild_id"]},
        )

    async def send(self, chat_id, message):
        chunks = chunk_message(message.text, max_length=2000)
        for chunk in chunks:
            await self.client.get_channel(int(chat_id)).send(chunk)

    async def on_unknown_user(self, msg):
        # Discord requires explicit allowlist — return None to reject
        return None
```

Discord has a transport quirk: alongside webhook-based interactions (slash commands), it needs a long-lived gateway connection for `on_message` (mentions, DMs, threads). The gateway part runs in a background task started at app boot — see [discord-channel.md](discord-channel.md) for full details + Cloud Run min-instances=1 requirement.

#### Email (Phase 1)

```python
# backend/channels/email_.py — underscore to avoid shadowing stdlib
class EmailChannel(BaseChannel):
    name = "email"
    command_prefix = "["               # parses [SkillName] from subject

    async def verify_webhook(self, headers, body):
        return verify_mailgun_signature(headers, body, MAILGUN_SIGNING_KEY)

    async def parse_inbound(self, payload):
        return InboundMessage(
            channel_user_id=payload["sender"],
            channel_chat_id=payload["Message-Id"],  # threading
            text=payload["body-plain"],
            attachments=[Attachment.from_mailgun(a) for a in payload.get("attachments", [])],
            raw=payload,
            metadata={"subject": payload["subject"], "in_reply_to": payload.get("In-Reply-To")},
        )

    async def select_skill(self, msg, firebase_uid):
        # [SkillName] in subject overrides default
        if cmd := CommandParser.parse(msg.metadata["subject"], prefix=self.command_prefix):
            if cmd.name == "skill":
                return await resolve_skill_by_name(cmd.args[0])
        return await super().select_skill(msg, firebase_uid)

    async def send(self, chat_id, message):
        await send_mailgun_reply(
            to=chat_id,
            subject=f"Re: {message.metadata.get('subject', '...')}",
            body=message.text,
            in_reply_to=chat_id,
        )
```

Ports cleanly from v5 `email_integration.py` — most of the work was wire-format handling, which becomes ~20 LOC inside `parse_inbound` + `send`. Quarto export ([PDF], [DOCX]) is handled by a skill, not the channel.

#### Telegram (Phase 2)

```python
# backend/channels/telegram_.py
class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def verify_webhook(self, headers, body):
        # Telegram uses a secret token in URL or header
        return headers.get("x-telegram-bot-api-secret-token") == TELEGRAM_WEBHOOK_SECRET

    async def parse_inbound(self, payload):
        update = Update.de_json(payload, self.bot)
        if not update.message or not update.message.text:
            return None
        return InboundMessage(
            channel_user_id=str(update.message.from_user.id),
            channel_chat_id=str(update.message.chat_id),
            text=update.message.text,
            attachments=[Attachment.from_telegram(a) for a in (update.message.photo or [])],
            raw=payload,
        )

    async def send(self, chat_id, message):
        for chunk in chunk_message(message.text, max_length=4096):
            await self.bot.send_message(chat_id=int(chat_id), text=chunk, parse_mode=ParseMode.HTML)
```

#### WhatsApp (Phase 2)

```python
class WhatsAppChannel(BaseChannel):
    name = "whatsapp"
    # Twilio webhook payload + send via twilio client; see file for full impl
```

### Webhook Endpoints

Auto-mounted by `ChannelRegistry.mount_webhooks(app)` — single call replaces N hardcoded `@app.post("/api/{name}/webhook")` declarations. Each registered channel gets `POST /api/{name}/webhook` with the framework's verify-then-dispatch flow.

```python
# backend/fast_api_app.py
ChannelRegistry.register(DiscordChannel())
ChannelRegistry.register(EmailChannel())
ChannelRegistry.register(TelegramChannel())
ChannelRegistry.register(WhatsAppChannel())
ChannelRegistry.mount_webhooks(app)
```

URLs are preserved (`/api/telegram/webhook`, `/api/email/webhook`, `/api/whatsapp/webhook`) — same paths as v5 by virtue of using the channel `name` as the URL segment. Discord adds `/api/discord/webhook` (new).

**Frontend proxying:** the existing `frontend/src/app/api/[...path]/route.ts` catch-all already forwards `/api/{channel}/webhook` to the backend — no per-channel Next.js route needed.

### Cross-Channel Sessions

**Decision: Channels and web share the same unified session scoped by `(user, skill, document)` — channel is metadata, not a partitioning key.**

See [chat-history.md](../v6.0.0/chat-history.md) (the source of truth) for the session schema and ID format. A Telegram user and a web user with the same Firebase UID interacting with the same skill + document see the **same conversation history**, not parallel threads.

This means:
- Documents uploaded via web are accessible via Telegram
- Preferences set in Telegram apply on web
- Conversation history is unified: asking a question on Telegram and continuing on web is a single thread
- `channel` is stored as an event-level attribute (which channel produced each message) for audit/analytics, not as a session key

```python
# Session ID format: see chat-history.md. Channel is event metadata, not part of the key.
```

### Architecture Diagram

```
[Telegram Bot API]          [Mailgun]              [Twilio]
    │                           │                      │
    ▼                           ▼                      ▼
/api/telegram/webhook    /api/email/webhook    /api/whatsapp/webhook
    │                           │                      │
    ▼                           ▼                      ▼
[TelegramChannel]        [EmailChannel]         [WhatsAppChannel]
    │                           │                      │
    └───────────┬───────────────┘──────────────────────┘
                │
                ▼
    [process_skill_request()]
        ├── Skill config (Firestore)
        ├── ADK Agent (model + tools)
        ├── Session (Firestore)
        └── Response (text-only for channels)
                │
                ▼
    [Channel-specific formatting]
        ├── Telegram: HTML, message splitting
        ├── Email: HTML email, Quarto export
        └── WhatsApp: plain text, media
```

## Implementation Plan

### Phase 0: Channel Framework (~1 day)
- [ ] `backend/channels/base.py` — `BaseChannel` ABC + `InboundMessage`/`OutboundMessage` models
- [ ] `backend/channels/registry.py` — `ChannelRegistry` with `register()` + `mount_webhooks()` + `get()`
- [ ] `backend/channels/commands.py` — `CommandParser` handling `/skill`, `/skills`, `/help`, `/clear` (and `[Name]` form for email)
- [ ] `backend/channels/attachments.py` — `AttachmentPipeline` (size guard → GCS → AILANG Parse → artifact)
- [ ] `backend/channels/identity.py` — `IdentityResolver` + `channel_identities/{channel}_{user_id}` Firestore collection
- [ ] Unit tests: `BaseChannel.handle_webhook` happy path with a `MockChannel`; command parsing per prefix; identity resolution; unknown-user gating
- [ ] Integration test: register `MockChannel`, mount, POST to `/api/mock/webhook`, verify end-to-end flow including `process_skill_request` invocation

**Phase 0 gate:** a `MockChannel` (~30 LOC, in `backend/tests/`) can be registered and webhook-handle a request without touching any real transport.

### Phase 1: Discord + Email (~1.5 days, in parallel)
- [ ] `backend/channels/discord.py` — `DiscordChannel` subclass + discord.py setup (slash commands + gateway for mentions); see [discord-channel.md](discord-channel.md) for details
- [ ] `backend/channels/email_.py` — `EmailChannel` subclass + Mailgun parse + send (lifts logic from v5 `email_integration.py`)
- [ ] Cloud Run `min-instances=1` Terraform module for Discord (gateway-keepalive requirement)
- [ ] `channel_routes/discord/{guild_id}` Firestore schema for per-guild skill defaults
- [ ] Integration tests: real Discord guild + Mailgun test webhook
- [ ] [an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md) is the first downstream consumer of both

**Phase 1 gate:** Discord bot in a test guild responds to `/ask` and a mention; email round-trip with `[Skill] body` subject parsing works.

### Phase 2: Telegram + WhatsApp (~1 day, follow-up)
- [ ] `backend/channels/telegram_.py` — `TelegramChannel` subclass (lifts logic from v5 `telegram_service.py`)
- [ ] `backend/channels/whatsapp.py` — `WhatsAppChannel` subclass (Twilio)
- [ ] Migrate `channel_mappings.py` data from v5 (phone↔email mapping for cross-channel identity)
- [ ] Integration tests: Telegram bot + Twilio sandbox

**Phase 2 gate:** v5 feature parity restored — every existing webhook URL works against v6 backend.

**Total: ~3.5 days.** Framework is the only non-cheap phase; channels 4+ are bounded ~4h each.

### Migration order rationale

Original v6.1.0 plan was Telegram first (active v5 users). New plan is Discord first because:
- [Shepherd / an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md) is the only real consumer in the v6 window — no v6 Telegram users exist yet
- Discord forces the framework to handle a non-pure-webhook transport (gateway), exposing more abstraction edges than Telegram would
- Email comes alongside Discord because it has the most-divergent skill-selection pattern (subject prefix) and exercises the override paths in the framework
- Telegram and WhatsApp port cleanly from v5 once the framework is proven — they don't add design surface, only deployment

## Migration & Rollout

**Webhook Preservation:**
- Same Cloud Run URLs → same webhook endpoints
- No need to reconfigure Telegram Bot API, Mailgun, or Twilio
- v6 just implements the same endpoint paths

**User Mapping:**
- `channel_mappings.py` from v5 maps phone numbers to emails
- Copy as data file, import during migration

**Rollback Plan:** Redeploy v5 (webhooks unchanged).

## Testing Strategy

### Backend Tests (pytest)
- [ ] Telegram: parse update → extract user + message → correct skill called
- [ ] Telegram: bot commands work (/skill, /skills)
- [ ] Email: parse Mailgun payload → extract sender, subject, body
- [ ] Email: skill extraction from subject prefix
- [ ] Message splitting for long responses
- [ ] User mapping (channel identity → Firebase UID)

### Integration Tests
- [ ] Telegram: send message via bot → receive skill response
- [ ] Email: send email → receive reply

## Security Considerations

- Telegram webhook verified via bot token (Telegram signs requests)
- Mailgun webhook verified via signing key
- Twilio webhook verified via auth token
- Channel user mapped to Firebase UID — same permission model as web

## Performance Considerations

- Channels are non-streaming (collect full response, then send)
- Response time dominated by agent execution (same as web)
- Message splitting adds <10ms overhead
- Webhook response must be fast (<5s) — process async if needed

## Success Criteria

- [ ] Telegram: message → skill response → formatted reply
- [ ] Email: email → skill response → reply email
- [ ] Bot commands work (/skill, /skills)
- [ ] Webhook URLs unchanged from v5
- [ ] Existing channel_mappings preserved
- [ ] All tests passing

## Open Questions

- File uploads: framework supports via `AttachmentPipeline`. Per-channel size limits (Discord 8MB free / 25MB Nitro, Telegram 50MB, email 25MB typical) — set per-channel `max_attachment_size` class attribute.
- Tool execution summaries in channel responses: defer to the skill. Some skills will want to surface "I searched Drive and found 3 contracts"; others will hide tool calls entirely. Channel framework doesn't decide; it just sends the skill's response text.
- "Channel mode" flag on skills to disable A2UI/MCP Apps: not needed — `channel` is in the skill request, and any rendering callback can branch on it. No new flag.
- Streaming-to-Discord (live message edits) — Discord supports it cleanly; other channels don't. Framework provides atomic `send()`; Discord adapter can override to add a `send_streaming()` that the agent factory calls when `channel.supports_streaming`. Defer until a fork needs it.
- `select_skill()` async ordering: should it run before or after command parsing? Currently: command parsing first, so `/skill X` always works regardless of channel's `select_skill` override. Locked in framework.

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Channel integrations (lines 710-714), webhook preservation (lines 769-775)
- [Agent Factory](agent-factory.md) — `process_skill_request()` lifecycle
- [Auth & Permissions](auth-and-permissions.md) — User mapping and permissions
- [Discord channel adapter](discord-channel.md) — Phase 1 detailed design
- [Event-driven skills](../v6.2.0/event-driven-skills.md) — trigger system depends on `ChannelRegistry.get(name).send()` being uniform
- [Audit log + analytics](../v6.2.0/audit-log-and-analytics.md) — uses `channel` event metadata written by `BaseChannel.handle_webhook`
- [Shepherd / an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md) — first downstream consumer
- v5 source: `<your-v5-source>/backend/email_integration.py`, `telegram_service.py`, `whatsapp_service.py`
- Discord scaffold: `<local-path>`
