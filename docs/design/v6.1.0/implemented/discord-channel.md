# Discord Channel Adapter

**Status**: ✅ Implemented 2026-05-16 (commit fa15281, merged in 51ea365)
**Priority**: P1 — first commercial fork needs it; generic enough to live in the template as the first non-trivial adapter
**Scope**: Backend channel adapter (`backend/channels/discord.py` 656 LOC) + chunk helper (56 LOC) + 4 test files (39 tests) + Cloud Run Terraform module (222 LOC across main.tf/variables.tf/outputs.tf)
**Dependencies**: [channels framework](channels.md) (shipped M1, commit 65aa951); [auth-and-permissions](../v6.0.0/implemented/auth-and-permissions.md)
**Created**: 2026-05-16
**Last Updated**: 2026-05-16 — sprint complete. 39 new tests pass (test_discord 19 + test_discord_streaming 7 + test_discord_registration 5 + test_chunk 8). `terraform validate` clean on `infrastructure/modules/cloud-run-channel/`. Registered in fast_api_app.py gated on `DISCORD_PUBLIC_KEY` so LOCAL_MODE boots cleanly. Sprint evaluator round 1: PASS 92/100. Deviations from this design: `/scope` command (the internal-tools fork-fork-specific) deferred — generic command set `/ask /skill /skills /help` covers the template. Gateway-message handler now uses `BaseChannel._dispatch_inbound` (extracted from the framework in commit 6c55b43 after the M2/M3 round-1 evaluations flagged duplication).

## Problem Statement

The [an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md) needs Discord as its primary user channel — that's where developer/team communication actually lives for internal-team forks. The bot mechanics already exist as working code at `<local-path>` (472 lines, Flask-era).

**This document covers only the Discord-specific adapter.** The shared plumbing (identity resolution, command parsing, attachment handling, webhook verification scaffolding, registry mounting) lives in the [channels framework](channels.md) and is consumed by all channel adapters uniformly. Discord = `BaseChannel` subclass + 3 abstract methods + ~80-120 LOC of discord.py specifics.

Pure-webhook helpers exist in [`sunholo-py/src/sunholo/bots/discord.py`](<local-path>) (`generate_discord_output`, `discord_webhook`) but those are one-way output formatters, not an interactive bot. The bulk of the work is the gateway-based interactive bot, which is what edmonbrain implements.

## Goals

**Primary:** Ship `backend/channels/discord.py` so any forked deployment can connect a Discord bot and route Discord guild events to skills.

**Success Metrics:**
- Adding Discord to a fork is one env-var (`DISCORD_TOKEN`) + one Terraform var (min-instances) + one Firestore doc (`channel_routes/discord/{guild_id}` → `skill_slug`)
- Mention + DM + slash-command flows all reach the same skill loop as web UI
- Streaming response edits the "Thinking..." message live (no batch-then-dump)
- Source citations render as Discord embeds, not inline noise
- Per-guild skill routing — different Discord servers can target different skills

**Non-Goals:**
- Discord OAuth login for the web UI (separate auth concern)
- Voice channel support (out of scope, no use case)
- Multi-bot-per-deployment (one bot, multiple guilds, fine)

## Design

### What the framework gives us (and we don't reimplement)

Anything in this list is **handled by the channels framework, not the Discord adapter**:

- Webhook verification scaffolding — adapter implements `verify_webhook()` but the framework calls it consistently
- Identity resolution — `IdentityResolver.resolve("discord", discord_user_id) → firebase_uid` via Firestore `channel_identities/discord_{user_id}`
- Command parsing — `/skill`, `/skills`, `/help`, `/clear` all work uniformly; adapter only needs to register Discord's slash-command UI for them
- Attachment handling — size guard, GCS upload, AILANG Parse, artifact registration via `AttachmentPipeline`
- Skill request flow — `BaseChannel.handle_webhook` calls `process_skill_request()` with all the right metadata
- Webhook auto-mounting — `ChannelRegistry.mount_webhooks(app)` mounts `POST /api/discord/webhook` for slash-command interactions
- Audit log entries — written by the framework with `channel="discord"` + adapter-supplied metadata

### What the Discord adapter implements

Three abstract methods plus discord.py-specific setup:

```python
# backend/channels/discord.py
class DiscordChannel(BaseChannel):
    name = "discord"
    max_attachment_size = 8 * 1024 * 1024  # 8MB Discord free tier

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.client.event(self.on_ready)
        self.client.event(self.on_message)  # gateway-based message handling
        self._gateway_task: asyncio.Task | None = None

    async def start_gateway(self):
        """Called once at app startup. Runs the gateway connection forever."""
        self._gateway_task = asyncio.create_task(self.client.start(DISCORD_TOKEN))

    # --- abstract methods ---

    async def verify_webhook(self, headers, body) -> bool:
        # Slash command interactions use Ed25519 signature
        return verify_ed25519(headers["x-signature-ed25519"], body, DISCORD_PUBLIC_KEY)

    async def parse_inbound(self, payload) -> InboundMessage | None:
        # Slash command interaction payload shape
        if payload.get("type") != 2:  # APPLICATION_COMMAND
            return None
        return InboundMessage(
            channel_user_id=str(payload["member"]["user"]["id"]),
            channel_chat_id=str(payload["channel_id"]),
            text=self._extract_command_text(payload),
            raw=payload,
            metadata={"guild_id": payload["guild_id"], "interaction_token": payload["token"]},
        )

    async def send(self, chat_id, message: OutboundMessage):
        chunks = chunk_message(message.text, max_length=2000)
        channel = self.client.get_channel(int(chat_id))
        for chunk in chunks:
            await channel.send(chunk)

    async def on_unknown_user(self, msg) -> str | None:
        # Discord requires explicit allowlist; consult guild membership + role
        return await check_discord_allowlist(msg.channel_user_id, msg.metadata["guild_id"])

    # --- discord.py gateway handler for mentions/DMs (not slash commands) ---

    async def on_message(self, message):
        if message.author == self.client.user:
            return
        if not isinstance(message.channel, discord.DMChannel) and self.client.user not in message.mentions:
            return
        # Re-enter the framework's handle_webhook path with a synthesised payload
        inbound = InboundMessage(
            channel_user_id=str(message.author.id),
            channel_chat_id=str(message.channel.id),
            text=message.content.replace(self.client.user.mention, "").strip(),
            attachments=[Attachment.from_discord(a) for a in message.attachments],
            raw=message_to_dict(message),
            metadata={"guild_id": str(message.guild.id) if message.guild else None,
                      "is_dm": isinstance(message.channel, discord.DMChannel)},
        )
        await self._handle_inbound_message(inbound)
```

The `on_message` path is Discord's gateway-specific quirk — most channels are purely webhook-driven, but Discord splits interactions (slash commands, signed webhooks) from messages (gateway, persistent connection). The adapter handles both; the framework path is the same.

### What we lift from edmonbrain (adapter-specific)

These bits are Discord-API specific, not framework-shareable:

- `discord.Client` setup with intents — exact intent flags for message_content + DM + attachments
- `on_message` handler shape — mention + DM detection logic
- **Auto-threading on first mention** — `f"{skill_slug}-zzz - {prefix}"` thread naming UX, valuable to keep
- `chunk_send()` — Discord 2000-char limit handling (the framework's generic chunker calls this)
- Discord attachment URL extraction (Discord serves attachments via CDN URLs)
- Source citation rendering as Discord embeds (`**source**:`, `**url**:`)
- Thread-history reconstruction for thread-discontinuity recovery

### What we don't carry forward from edmonbrain

- **Flask transport** — backend talk now goes via the framework's `process_skill_request()` call; no HTTP hop
- **Custom `###JSON_START###`/`###JSON_END###` delimiters** — replaced by AG-UI events (`TEXT_DELTA`, `TOOL_CALL`, `RUN_FINISHED`)
- **`select_vectorname()` config.json lookup** — replaced by per-guild `channel_routes/discord/{guild_id}` Firestore doc, consumed by `select_skill()` override
- **`!debug`/`!vectorname` Mark-only commands** — replaced by standard `/skill <slug>` slash command for everyone
- **`€€Question€€` agent-to-agent protocol** — replaced by A2A discovery (if needed at all)

### Streaming response — adapter-specific override

The base framework's `send()` is atomic. Discord supports live message edits, which makes streaming feel snappy. `DiscordChannel` overrides with a `send_streaming(chat_id, sse_stream)` method that:

1. Sends "Thinking..." as the first message
2. Subscribes to the AG-UI SSE stream from `process_skill_request()`
3. On each `TEXT_DELTA`, edits the message (batched to ≤1Hz to respect rate limits)
4. On `TOOL_CALL`, appends a "🔍 Searching Drive..." progress line
5. On `RUN_FINISHED`, sends final edit with citations as separate messages

The agent factory checks `channel.supports_streaming` and routes to `send_streaming()` if available, else `send()`.

### Sessions

`{guild_id, channel_id, thread_id}` → ADK `session_id` via deterministic hash. Threading model: one Discord thread = one ADK session. A new mention in a non-thread channel creates a new thread + new session.

### Hosting

**Critical:** Discord rejects bots that don't respond to gateway pings within ~1s. Cloud Run cold starts will break the connection.

- Cloud Run **min-instances=1** (cost: ~10 EUR/month on smallest tier)
- Alternative: deploy as a Cloud Run service with a `KEEPALIVE` cron pinging the bot's health endpoint every 4 min, but min-instances is the cleaner answer
- Document the cost trade-off in the template README

Add to `infrastructure/modules/cloud-run-channel/` Terraform module with `min_instances` variable defaulting to 0 (override for Discord).

### Slash commands

Initial set:
- `/ask <question>` — synchronous Q&A in the channel
- `/scope <client>` — shortcut to contract-Q&A skill (the internal-tools fork-specific; template gets a generic version)
- `/skill <slug>` — explicit skill selection for the next message
- `/help` — list available skills + slash commands

Slash commands are registered per-guild on bot start. Idempotent — re-registering on redeploy is fine.

### Audit log integration

Every message → audit log entry via the shared callback ([audit-log-and-analytics.md](../v6.2.0/audit-log-and-analytics.md)), with `channel="discord"`, `guild_id`, `channel_id`, `thread_id`, `discord_user_id`. Maps to internal Sheep identity for per-person attribution.

## Implementation Plan

**Prerequisite:** [Phase 0 of channels.md](channels.md#phase-0-channel-framework-1-day) must land first. Discord adapter cannot be implemented before `BaseChannel`, `ChannelRegistry`, `CommandParser`, `AttachmentPipeline`, and `IdentityResolver` exist.

7h total once framework lands, single PR target.

| Step | Est | Notes |
|------|-----|-------|
| `DiscordChannel` subclass — implement `verify_webhook`, `parse_inbound`, `send`, `on_unknown_user` | 1h | Framework absorbs identity/commands/attachments boilerplate |
| Gateway connection (start_gateway, on_message handler) for mentions + DMs | 1.5h | Discord-quirk: gateway alongside slash-command webhook |
| AG-UI SSE consumer + `send_streaming()` override — live message edit pattern | 3h | Net-new; replaces edmonbrain's chunked-HTTP parser |
| Slash command registration (`/ask`, `/skill`, `/skills`, `/help`) via `discord.app_commands` | 1h | Per-guild registration on bot start; idempotent |
| Source citation rendering as Discord embeds + auto-thread UX | 0.5h | Lift from edmonbrain |

Plus (template-level, counted once in framework):
- Terraform module `infrastructure/modules/cloud-run-channel/` with `min_instances` variable (Discord requires 1)
- Firestore `channel_routes/discord/{guild_id}` schema for per-guild skill defaults
- Smoke test: bot joins a guild, responds to `/ask`, streams reply, logs to audit

## Risk Register

(Framework-handled risks — `BaseChannel` enforcement, identity resolution, command parsing — are not duplicated here; see [channels.md §Risk Register](channels.md).)

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| discord.py version churn | Low | Pin major; CI gate catches break |
| Cold-start drops gateway connection | High | Mandatory `min_instances=1`; alarm on gateway-disconnect events |
| Slash command registration race on parallel deploys | Low | Lock to first instance via Firestore flag |
| Per-guild skill misrouting | Medium | Show `**skill: <slug>**` header on first reply in a thread; framework's audit log captures every routing decision |
| Streaming edit hits Discord rate limits on long replies | Medium | Batch edits to ≤1Hz; final atomic edit after `RUN_FINISHED` |
| Gateway and slash-command paths diverge in behaviour | Medium | Both funnel into the same `handle_webhook` flow via a shared `_handle_inbound_message()` helper; integration tests exercise both |

## Testing Strategy

- Unit: AG-UI event → Discord message transformation
- Integration: real Discord bot in a test guild, mention + DM + slash command happy paths
- Adversarial: mention bot from a non-allowlisted user; confirm rejection
- Cost: 7-day soak with min-instances=1 to confirm Cloud Run cost matches estimate

## Security Considerations

- Discord bot token in Secret Manager, not env
- Discord OAuth callback uses state token (no replay)
- Allowlist Discord user IDs → Sheep identity via Firestore (no anonymous access to skills)
- Rate-limit per-user via the existing ADK quota callback
- Slash-command permission scoping (e.g., `/skill` admin-only) via Discord's built-in role permissions

## Open Questions

1. **discord.py vs Pycord vs interactions.py?** discord.py is the de-facto modern fork; edmonbrain uses it. Stick with discord.py unless slash-command ergonomics push us to Pycord.
2. **One bot per fork or one bot per template instance?** Recommendation: one bot per fork (per deployment). Multi-tenancy is an explicit non-goal.
3. **Voice/audio in v0.2.0?** Defer unless a use case appears.

## Related Documents

- **[Channels framework](channels.md)** — Parent design; `BaseChannel` ABC + registry + shared plumbing. **Must land first (Phase 0).**
- [an internal-tools fork scope](../forks/internal-tools-fork/v0.1.0/scope.md) — first consumer
- [Audit log + analytics](../v6.2.0/audit-log-and-analytics.md) — captures Discord events via the framework's standard channel metadata
- [Event-driven skills](../v6.2.0/event-driven-skills.md) — uses `ChannelRegistry.get("discord").send()` for trigger output routing
- Source scaffold: [<local-path>](<local-path>)
- Helper (webhook output formatter, not bot): [<local-path>](<local-path>)
