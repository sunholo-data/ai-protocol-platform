# Sprint Plan: CHANNELS-FRAMEWORK — v6.1.0 Sprint 1.6

## Summary

Ship a `BaseChannel` framework + four channel adapters (Discord, Email, Telegram, WhatsApp) so any fork can wire a messaging channel through `process_skill_request()` in ~4h. Unblocks the Shepherd / an internal-tools fork (Discord) and the channel-output side of event-driven skills.

**Duration:** 4 calendar days (3.5d focused effort + 0.5d slack for workshop-polish context switches)
**Scope:** Backend (primary) + Terraform (Cloud Run min-instances module) + ~50 LOC frontend (proxy verification only)
**Dependencies:** agent-factory ✅, auth-and-permissions ✅, skills-data-model ✅, chat-history ✅, AILANG Parse integration ✅ — all in place
**Risk Level:** Medium — Discord gateway + min-instances setup has the most net-new infrastructure; framework abstraction has design risk if wrong
**Design Docs:** [channels.md](channels.md) (framework + adapters) + [discord-channel.md](discord-channel.md) (adapter detail)

## Current Status Analysis

### Recent Velocity (last 14 days, 25 commits)
- Average focused-day backend output: ~1100 LOC including tests (M1 LOCAL_MODE shipped 1672 LOC in ~1d)
- Average focused-day frontend output: ~750 LOC (M2 LOCAL_MODE shipped 377 LOC in 0.5d)
- Recent milestone hit rate: 5/5 on LOCAL-MODE-AND-FORK (all green, all CI parity)
- Most recent commits show ruff/CI parity discipline already in place — pre-push gotcha lesson absorbed

### Estimated Capacity for This Sprint
- 3.5 focused days × ~1000 backend LOC/day = ~3500 LOC budget
- Sprint estimate (below): ~3550 LOC implementation + ~1670 LOC tests = ~5220 LOC total
- Includes ~30% reuse from v5 `email_integration.py`, `telegram_service.py`, edmonbrain `discord-bot/bot.py`
- Workshop-polish parallel work → assume 70% focus → 4-5 calendar days realistic

### Existing Implementation
- `backend/channels/__init__.py` exists, empty
- v5 references available at `<your-v5-source>/backend/`:
  - `email_integration.py` (~340 LOC, port target)
  - `telegram_service.py` (~280 LOC, port target)
  - `whatsapp_service.py` (~210 LOC, port target)
  - `channel_mappings.py` (~120 LOC, data migration target)
- Discord scaffold at `<local-path>` (~472 LOC, lift target)
- sunholo-py helpers at `<local-path>` (webhook formatter only, not bot)
- AG-UI SSE protocol already in `backend/protocols/streaming.py`
- `process_skill_request()` exposed via agent factory + skills route
- AILANG Parse pipeline at `backend/tools/parsing.py`
- GCS artifact pipeline exists (`backend/db/artifacts.py`)
- Firebase auth context + role tags ready
- Frontend `/api/[...path]/route.ts` catch-all already forwards `/api/{channel}/webhook` to backend (no per-channel frontend routes needed)

## Proposed Milestones

### M1: Channels Framework (Phase 0) — ✅ Shipped 2026-05-16 (commit 65aa951)
**Scope:** backend
**Goal:** Land the `BaseChannel` framework so any subsequent channel adapter is ~80 LOC of channel-specific code over shared plumbing
**Estimated:** ~750 LOC implementation + ~600 LOC tests = ~1350 LOC
**Actual:** 2543 LOC total (8 implementation files + 7 test files + 4 config-modify files). Three helper modules (`_default_skill.py`, `_commands_runtime.py`, `_skill_invoke.py`) emerged during implementation. `resolve_channel_bucket()` added to `db/clients.py` for channel uploads without a per-user email domain. 74 new tests pass; full backend suite went from 871 → 945. Sprint evaluator round 1: PASS 95/100.
**Duration:** 1 day (sequential blocker — no parallelism within M1) — completed in single session

**Tasks:**
- [ ] `backend/channels/base.py` — `BaseChannel` ABC + `InboundMessage`/`OutboundMessage`/`Attachment` Pydantic models (~150 LOC)
- [ ] `backend/channels/registry.py` — `ChannelRegistry.register/get/mount_webhooks` (~80 LOC)
- [ ] `backend/channels/commands.py` — `CommandParser` with `/` and `[` prefixes, handles `/skill`, `/skills`, `/help`, `/clear` (~120 LOC)
- [ ] `backend/channels/attachments.py` — `AttachmentPipeline.upload` (size guard → GCS → AILANG Parse → artifact register) (~180 LOC)
- [ ] `backend/channels/identity.py` — `IdentityResolver.resolve` + `channel_identities/{channel}_{user_id}` Firestore collection (~120 LOC)
- [ ] Firestore rules update for `channel_identities` (~30 LOC)
- [ ] `backend/tests/channels/test_base.py` — `MockChannel` fixture + happy-path `handle_webhook` flow (~150 LOC tests)
- [ ] `backend/tests/channels/test_registry.py` — register, get, mount auto-creates `/api/{name}/webhook` (~80 LOC tests)
- [ ] `backend/tests/channels/test_commands.py` — parse `/skill foo`, `[Doc Analyst]`, `/help`, malformed inputs (~100 LOC tests)
- [ ] `backend/tests/channels/test_attachments.py` — size guard, type detection, AILANG Parse routing (~120 LOC tests)
- [ ] `backend/tests/channels/test_identity.py` — resolve hit, miss → on_unknown_user, allowlist override (~100 LOC tests)
- [ ] `backend/tests/channels/test_handle_webhook_integration.py` — MockChannel + register + mount + POST → process_skill_request stub (~120 LOC tests)
- [ ] CI parity: `make lint && make test-fast` clean

**Files to Create:**
- `backend/channels/base.py` (new, ~150)
- `backend/channels/registry.py` (new, ~80)
- `backend/channels/commands.py` (new, ~120)
- `backend/channels/attachments.py` (new, ~180)
- `backend/channels/identity.py` (new, ~120)
- `backend/channels/__init__.py` (modify, ~30 — exports + register hook)
- `backend/tests/channels/test_base.py` (new, ~150)
- `backend/tests/channels/test_registry.py` (new, ~80)
- `backend/tests/channels/test_commands.py` (new, ~100)
- `backend/tests/channels/test_attachments.py` (new, ~120)
- `backend/tests/channels/test_identity.py` (new, ~100)
- `backend/tests/channels/test_handle_webhook_integration.py` (new, ~120)

**Files to Modify:**
- `backend/fast_api_app.py` — add `ChannelRegistry.mount_webhooks(app)` call after channel imports (~10 LOC delta)
- `firestore.rules` — add `channel_identities` rules (~30 LOC delta)

**Acceptance Criteria:**
- [ ] `MockChannel` can be registered and `POST /api/mock/webhook` reaches `process_skill_request()` (stubbed)
- [ ] `CommandParser.parse("/skill foo")` and `CommandParser.parse("[Doc Analyst] body", prefix="[")` both succeed
- [ ] `IdentityResolver.resolve("telegram", "12345")` → returns Firebase UID; on miss → calls `on_unknown_user`
- [ ] `AttachmentPipeline.upload` rejects > size-limit, uploads to GCS, calls AILANG Parse for `.pdf`/`.docx`, registers artifact, returns IDs
- [ ] All 12 unit/integration tests pass
- [ ] `cd backend && make lint && make test-fast` clean (CI parity)
- [ ] `verify_rules.py` passes with new `channel_identities` rules

**Risks:**
- BaseChannel API ends up wrong → Mitigation: review against discord-channel.md adapter sketch and v5 email_integration.py before locking; the MockChannel + integration test acts as the contract
- `process_skill_request` interface changes mid-sprint → Mitigation: import via stable interface from agent factory; if change needed, do it in one commit so all channels move together
- AttachmentPipeline → AILANG Parse coupling regresses parse pipeline → Mitigation: reuse existing `backend/tools/parsing.py` entry point unchanged, only add the pipeline wrapper

---

### M2: Discord Adapter (Phase 1a) — ✅ Shipped 2026-05-16 (commit fa15281, merged 51ea365)
**Scope:** backend + Terraform
**Goal:** Discord bot in test guild responds to `/ask` slash command and to @mentions in threads, streaming responses with live message edits
**Estimated:** ~640 LOC implementation + ~370 LOC tests + ~40 LOC Terraform = ~1050 LOC
**Actual:** 1646 LOC (656 adapter + 56 chunk helper + 712 tests + 222 terraform). 39 new tests. `terraform validate` clean. Sprint evaluator round 1: PASS 92/100. Deviation: `/scope` (the internal-tools fork-fork-specific) deferred — generic command set used instead. Parallel Task sub-agent.
**Duration:** 1 day (depends on M1; can run **parallel** with M3 email) — completed in parallel with M3 in a single session

**Tasks:**
- [ ] `backend/channels/discord.py` — `DiscordChannel` subclass + discord.py setup (intents, client, gateway task) (~180 LOC)
- [ ] `verify_webhook` Ed25519 signature for slash-command interactions (~30 LOC)
- [ ] `parse_inbound` for slash-command payload + `on_message` gateway-handler-to-framework adapter (~120 LOC)
- [ ] `send` with `chunk_message` + Discord 2000-char split (~50 LOC, includes `backend/channels/_chunk.py` helper)
- [ ] AG-UI SSE consumer + `send_streaming` override — "Thinking..." → live edit → final + citations (~150 LOC)
- [ ] Slash command registration on bot start — `/ask`, `/skill`, `/skills`, `/help` (~80 LOC)
- [ ] `on_unknown_user` — guild allowlist check against Firestore `channel_routes/discord/{guild_id}` (~30 LOC)
- [ ] Source citation rendering as Discord embeds (~60 LOC)
- [ ] `infrastructure/modules/cloud-run-channel/main.tf` — Cloud Run service with `min_instances` variable (~40 LOC)
- [ ] `backend/tests/channels/test_discord.py` — parse_inbound slash + on_message, verify_webhook, send chunking, on_unknown_user allowlist (~200 LOC tests)
- [ ] `backend/tests/channels/test_discord_streaming.py` — AG-UI event → message edit transformation (~120 LOC tests)
- [ ] `backend/tests/channels/test_discord_registration.py` — slash-command registration idempotency (~50 LOC tests)
- [ ] CI parity check

**Files to Create:**
- `backend/channels/discord.py` (new, ~430)
- `backend/channels/_chunk.py` (new, ~30 — shared chunker used by Discord + Telegram)
- `backend/tests/channels/test_discord.py` (new, ~200)
- `backend/tests/channels/test_discord_streaming.py` (new, ~120)
- `backend/tests/channels/test_discord_registration.py` (new, ~50)
- `infrastructure/modules/cloud-run-channel/main.tf` (new, ~40)
- `infrastructure/modules/cloud-run-channel/variables.tf` (new, ~30)
- `infrastructure/modules/cloud-run-channel/outputs.tf` (new, ~15)

**Files to Modify:**
- `backend/fast_api_app.py` — `ChannelRegistry.register(DiscordChannel())` (~3 LOC)
- `backend/secrets.py` (or equivalent) — load `DISCORD_TOKEN`, `DISCORD_PUBLIC_KEY` from Secret Manager (~15 LOC)
- `backend/Makefile` — add `make discord-register-commands` convenience target (~5 LOC)
- `.env.example` — add Discord env vars (~5 LOC delta)

**Acceptance Criteria:**
- [ ] Real Discord bot in a test guild responds to `/ask hello` → reply appears within 2s
- [ ] `@bot what's up?` in a thread starts a new ADK session and streams reply with live message edits
- [ ] DM to the bot routes to user's default skill
- [ ] Bot cold-start does not drop gateway (Cloud Run `min_instances=1` verified via Terraform plan)
- [ ] Non-allowlisted Discord user gets rejection message, no skill invocation
- [ ] All Discord-specific tests pass; integration test against mock Discord API green
- [ ] CI parity clean (`make lint && make test-fast`)
- [ ] `infrastructure/modules/cloud-run-channel/` plan applies cleanly to a test env

**Risks:**
- discord.py gateway connection unstable on Cloud Run → Mitigation: `min_instances=1` + reconnection loop in `start_gateway`; alarm on gateway-disconnect event count
- Slash command registration race on parallel deploys → Mitigation: Firestore `bot_state/discord` flag with TTL — only first instance registers
- AG-UI → Discord edit hits rate limit on long streams → Mitigation: batch edits to ≤1Hz; final atomic edit after `RUN_FINISHED`
- Cost surprise from `min_instances=1` → Mitigation: documented ~10 EUR/month upfront; alert on actual >20 EUR

---

### M3: Email Adapter (Phase 1b) — ✅ Shipped 2026-05-16 (commit 4bc1d3b, merged 3b31ccc)
**Scope:** backend
**Goal:** Email round-trip works — send `[SkillName] body` to bot address, receive reply from the same skill
**Estimated:** ~310 LOC implementation + ~240 LOC tests = ~550 LOC
**Actual:** 466 LOC (280 adapter + 186 tests). 28 new tests. Sprint evaluator round 1: PASS 94/100. v5 logic intentionally dropped: Quarto export flags (skill concern), HTML formatting (deferred), `assistant-{id}@domain` routing (replaced by `[SkillName]` subject), `EmailRateLimiter` (FastAPI middleware concern). Framework gap surfaced + fixed: `OutboundMessage` now propagates `inbound.metadata` for In-Reply-To threading (commit 6c55b43). Parallel Task sub-agent.
**Duration:** 0.5 day (depends on M1; runs **parallel** with M2 Discord) — completed in parallel with M2 in a single session

**Tasks:**
- [ ] `backend/channels/email_.py` — `EmailChannel` subclass (note underscore to avoid stdlib shadow) (~150 LOC)
- [ ] `verify_webhook` Mailgun signature verification (~40 LOC)
- [ ] `parse_inbound` from Mailgun JSON payload + attachment extraction (~60 LOC)
- [ ] `select_skill` override — `[Skill Name]` from subject via `CommandParser` (~30 LOC)
- [ ] `send` via Mailgun API with reply threading (`In-Reply-To`, `References`) (~80 LOC)
- [ ] Port v5 logic from `email_integration.py` — Sunholo strip + framework adaptation
- [ ] `backend/tests/channels/test_email.py` — Mailgun signature verify, parse, subject prefix, send round-trip (~180 LOC tests)
- [ ] `backend/tests/channels/test_email_attachments.py` — multi-attachment upload via AttachmentPipeline (~60 LOC tests)
- [ ] CI parity check

**Files to Create:**
- `backend/channels/email_.py` (new, ~310)
- `backend/tests/channels/test_email.py` (new, ~180)
- `backend/tests/channels/test_email_attachments.py` (new, ~60)

**Files to Modify:**
- `backend/fast_api_app.py` — register `EmailChannel()` (~3 LOC)
- `backend/secrets.py` — load `MAILGUN_SIGNING_KEY`, `MAILGUN_API_KEY`, `EMAIL_SENDER_ADDRESS` (~10 LOC)
- `.env.example` — Mailgun env vars (~5 LOC)

**Acceptance Criteria:**
- [ ] Sending an email to bot address with subject `[General Assistant] hello` → reply email received from the General Assistant skill
- [ ] Subject `Re: previous` → reply threads correctly (`In-Reply-To` set)
- [ ] PDF attachment → uploaded via AttachmentPipeline → AILANG-parsed → skill sees the document
- [ ] Mailgun signature failure → 401, no skill invocation
- [ ] All email tests pass
- [ ] CI parity clean

**Risks:**
- Mailgun signature verification mismatch with v5 → Mitigation: copy exact verification logic from v5 `email_integration.py`; test against captured-payload fixture
- HTML email formatting edge cases (replies, quoted text) → Mitigation: parse plain-text body for v1; HTML formatting is a follow-up
- Sender domain not configured → Mitigation: document required Mailgun domain setup in `.env.example` and `docs/integrations/email-setup.md`

---

### M4: Telegram + WhatsApp Adapters (Phase 2) — ✅ Shipped 2026-05-16 (commit 1a592bd, merged dc01eaf)
**Scope:** backend
**Goal:** v5 feature parity — every existing v5 webhook URL works against the new framework
**Estimated:** ~380 LOC implementation + ~260 LOC tests + ~80 LOC migration = ~720 LOC
**Actual:** 1517 LOC (337 telegram + 327 whatsapp + 227 migration script + 282+285 tests + ~50 wiring). 43 new tests. Sprint evaluator round 1: PASS 90/100. v5 logic intentionally dropped: Sunholo + LangChain imports, voice/Gemini Live (out of channel scope), `first_impression` routing, per-user rate limiting (FastAPI middleware concern). python-telegram-bot + twilio packages added. Sub-agent stopped at token limit having drafted all 5 files (tests passing) but not committed; main session finalised commit + added fast_api_app.py/.env.example wiring directly. Parallel Task sub-agent.
**Duration:** 1 day (depends on M1; can run parallel with M2+M3 in principle, but more efficient after M3 proves the v5-port pattern) — completed in parallel with M5

**Tasks:**
- [ ] `backend/channels/telegram_.py` — `TelegramChannel` subclass (~180 LOC)
- [ ] Port v5 `telegram_service.py` — strip Sunholo, lift HTML formatting + photo handling
- [ ] `backend/channels/whatsapp.py` — `WhatsAppChannel` subclass (Twilio) (~150 LOC)
- [ ] Port v5 `whatsapp_service.py` — strip Sunholo
- [ ] Migrate `channel_mappings.py` — phone↔email mapping data → Firestore `channel_identities` initial seed (~50 LOC)
- [ ] `backend/tests/channels/test_telegram.py` — parse update, verify secret token, send with HTML, photo attachment (~150 LOC tests)
- [ ] `backend/tests/channels/test_whatsapp.py` — Twilio webhook parse, signature verify, send (~110 LOC tests)
- [ ] `backend/scripts/migrate_v5_channel_mappings.py` — one-shot migration script (~80 LOC) + dry-run mode
- [ ] CI parity check

**Files to Create:**
- `backend/channels/telegram_.py` (new, ~180)
- `backend/channels/whatsapp.py` (new, ~150)
- `backend/scripts/migrate_v5_channel_mappings.py` (new, ~80)
- `backend/tests/channels/test_telegram.py` (new, ~150)
- `backend/tests/channels/test_whatsapp.py` (new, ~110)

**Files to Modify:**
- `backend/fast_api_app.py` — register `TelegramChannel()` + `WhatsAppChannel()` (~5 LOC)
- `backend/secrets.py` — Telegram + Twilio creds (~15 LOC)

**Acceptance Criteria:**
- [ ] Sending a Telegram message to the v5 bot URL → reaches skill via the v6 backend; reply HTML-formatted
- [ ] Photo attached to Telegram message → ingested via AttachmentPipeline
- [ ] WhatsApp test message via Twilio sandbox → skill response delivered
- [ ] Migration script in `--dry-run` mode shows the v5 mapping → Firestore plan; live run completes for v5 data
- [ ] All tests pass
- [ ] CI parity clean

**Risks:**
- v5 webhook URL change → Mitigation: webhook URLs are channel-name-based, preserved via `ChannelRegistry` mount; smoke test against actual v5 URL
- WhatsApp Twilio quota differs from v5 setup → Mitigation: doc the env vars required; defer multi-region if Twilio becomes the bottleneck
- `channel_mappings.py` v5 schema drift → Mitigation: dry-run mode + diff against current v5 export before live migration

---

### M5: Smoke + Integration Test + Channel-Adapter Howto Doc — ✅ Shipped 2026-05-16 (commit 19c8335, merged ce0aae0)
**Scope:** backend + docs
**Goal:** Prove the "new channel in ~4h" claim by writing the runbook *while* adding a tiny demo channel
**Estimated:** ~120 LOC implementation + ~80 LOC tests + ~250 LOC docs = ~450 LOC
**Actual:** 843 LOC (131 `_demo_cli.py` + 293 test_smoke_all_channels.py + 345 howto doc + 72 smoke-script extension + 2 cross-link). 10 new tests. Sprint evaluator round 1: PASS 96/100. Howto authored *by* following itself to implement `_demo_cli.py` — zero framework friction (the M2/M3 cleanup commit 6c55b43 had already paid down the gaps that would have shown up). CliDemoChannel is INTERNAL — never registered in fast_api_app.py; exists only as the howto's worked example. Parallel Task sub-agent.
**Duration:** 0.5 day (after M2+M3; can run parallel with M4 in principle) — ran parallel with M4

**Tasks:**
- [ ] `backend/channels/_demo_cli.py` — tiny "CLI channel" adapter that reads stdin/writes stdout (the *demo* for the howto; ~80 LOC)
- [ ] `backend/tests/channels/test_smoke_all_channels.py` — register all 4 + CLI demo, mount, smoke each `/api/{name}/webhook` endpoint with synthetic payloads (~80 LOC tests)
- [ ] Extend `scripts/smoke-deployed.sh` — add channel reachability checks (~30 LOC delta)
- [ ] Verify frontend `/api/[...path]/route.ts` catch-all forwards Discord+email+telegram+whatsapp webhooks correctly (read-only sanity — no LOC delta expected)
- [ ] **Write `docs/integrations/channels-adapter-howto.md`** (~250 LOC docs) — step-by-step "adding a new channel" using the CLI demo as the worked example
- [ ] Update `docs/design/v6.1.0/channels.md` with link to the howto doc

**Files to Create:**
- `backend/channels/_demo_cli.py` (new, ~80)
- `backend/tests/channels/test_smoke_all_channels.py` (new, ~80)
- `docs/integrations/channels-adapter-howto.md` (new, ~250)

**Files to Modify:**
- `scripts/smoke-deployed.sh` (~30 LOC delta)
- `docs/design/v6.1.0/channels.md` (link to howto, ~5 LOC delta)

**Acceptance Criteria:**
- [ ] `scripts/smoke-deployed.sh dev all` passes; channel endpoints respond 200 on valid + 401 on missing signature
- [ ] CLI demo channel works end-to-end: `python -m backend.channels._demo_cli` lets you chat with a skill from terminal
- [ ] Howto doc is self-contained — a fresh developer can follow it without re-reading channels.md
- [ ] Linked from channels.md design doc

**Risks:**
- CLI demo adds awkward complexity → Mitigation: keep it small (~80 LOC) and explicitly mark internal/demo-only
- Howto becomes stale fast → Mitigation: end-to-end test in `test_smoke_all_channels.py` references the howto file paths; if files move, test fails

---

## Day-by-Day Breakdown

### Day 1 — M1: Channels Framework (sequential blocker)
- **Focus:** Land BaseChannel ABC + Registry + CommandParser + AttachmentPipeline + IdentityResolver
- **Morning:** `base.py` + `registry.py` + Firestore rules + their tests (write tests first — TDD enforced by the abstract method contract)
- **Afternoon:** `commands.py` + `attachments.py` + `identity.py` + their tests; integration test via `MockChannel`
- **Checkpoint:** `cd backend && make lint && make test-fast` green; new test file count = 6; MockChannel + handle_webhook flow demonstrates end-to-end framework

### Day 2 — M2 + M3: Discord + Email in parallel
- **Focus:** Two adapters on top of M1 framework
- **Morning track A (Discord):** `discord.py` subclass + `_chunk.py` + slash command registration + Ed25519 verify; tests against captured-payload fixtures
- **Morning track B (Email):** `email_.py` subclass + Mailgun verify + parse_inbound + send via Mailgun API; lift v5 logic
- **Afternoon:** AG-UI SSE → Discord live-edit streaming + Discord embed citation rendering (Discord-track); attachment routing via AttachmentPipeline tests (email track)
- **Checkpoint:** Both adapters' unit tests pass; CI parity green; manual smoke against real Discord test guild + Mailgun sandbox

### Day 3 — M4 + Cloud Run module: Telegram + WhatsApp + Infra polish
- **Focus:** v5 parity adapters + Terraform module finalisation
- **Morning:** Port `telegram_.py` + `whatsapp.py` from v5; minimal new code because framework absorbs most of the v5 boilerplate
- **Afternoon:** `cloud-run-channel/` Terraform module finalisation + `min_instances=1` variable wiring for Discord; v5 `channel_mappings.py` migration script + dry-run
- **Checkpoint:** All 4 adapter tests pass; Terraform `plan` for Discord deployment clean; migration dry-run reads v5 export without errors

### Day 4 — M5: Smoke + Howto Doc + Wrap
- **Focus:** Prove the "new channel in ~4h" promise + ship the runbook
- **Morning:** Write `docs/integrations/channels-adapter-howto.md` *by* implementing `_demo_cli.py` while following the doc step-by-step
- **Afternoon:** Smoke test extensions + run `scripts/smoke-deployed.sh` against dev env; final CI parity check; verify rules; close out sprint JSON
- **Checkpoint:** All milestones marked passed in sprint JSON; `cd backend && make lint && make test-fast` green; `cd frontend && npm run quality:check` green; smoke script green; sprint plan reviewed for next-step handoff (an internal-tools fork can start)

### Day 5 (buffer) — Workshop polish swap-in
- **Focus:** Slack for workshop-polish work without blocking the channels sprint completion
- This day is the "workshop polish runs alongside" budget. If channels takes 4d clean, Day 5 frees up for [mcp-app-render-ux Phase A](mcp-app-render-ux.md) or [a2ui-workshop-demo](a2ui-workshop-demo.md) progress.

## Quality Gates

After each milestone:
```bash
cd backend && make lint && make test-fast    # CI parity per pre-push rule
```

After M5 (sprint close):
```bash
cd backend && make lint && make test-fast        # full backend
cd frontend && npm run quality:check             # full frontend (lint + typecheck + tests + build)
./scripts/smoke-deployed.sh dev all              # post-deploy smoke
make verify-rules                                # Firestore rules runner
```

**Pre-push CI parity discipline** (per [CLAUDE.md](../../../CLAUDE.md) Pre-push gotcha) — `quality:check:fast` and `make-lint-only` are NOT enough; this sprint commits to using the parity commands at every milestone close.

## Success Metrics

- [ ] All 4 channel adapters in `backend/channels/` deployed and reachable on dev env
- [ ] `BaseChannel` ABC + registry: subclassing produces working `/api/{name}/webhook` mount with zero per-channel routing code
- [ ] `docs/integrations/channels-adapter-howto.md` is the proof — a fresh adapter (CLI demo) was built by following it
- [ ] 8 new test files; ~1670 LOC tests; 100% green on `make test-fast`
- [ ] Frontend `/api/[...path]` catch-all forwards channel webhooks correctly (no per-channel route added)
- [ ] CI parity gates clean throughout
- [ ] Shepherd / an internal-tools fork can now start without channel-framework blockers
- [ ] [Event-driven skills (2.6)](../v6.2.0/event-driven-skills.md) and [audit-log-and-analytics (2.7)](../v6.2.0/audit-log-and-analytics.md) can start in parallel — `BaseChannel.handle_webhook` writes the `channel` field they consume

## Dependencies

- agent-factory ✅ (v6.0.0 1A.2)
- auth-and-permissions ✅ (v6.0.0 1A.1)
- skills-data-model ✅ (v6.0.0 0.2)
- chat-history ✅ (v6.0.0 1A.6 + v6.1.0 1.8)
- AILANG Parse integration ✅
- GCS artifact pipeline ✅
- AG-UI SSE protocol ✅

## Open Questions

- **Discord OAuth-vs-allowlist for `on_unknown_user`** — locked at "allowlist via Firestore" for v1 (matches Shepherd's invite-only Sheep model). Revisit if a public-facing fork needs anonymous user onboarding.
- **Streaming for non-Discord channels** — locked at "atomic send only" for v1. Discord uses `send_streaming` override. Telegram has some edit support; defer if a fork needs it.
- **CLI demo channel as a permanent fixture** — kept in `backend/channels/_demo_cli.py` (underscore-prefixed = internal); doubles as a debugging tool and the howto's worked example.
- **WhatsApp deployment** — Twilio sandbox is enough for the sprint gate. Production WhatsApp Business approval is out of scope; document the steps required for a fork to wire up real WhatsApp.

## Notes

- This sprint is **pure backend + Terraform + docs**; frontend changes limited to verifying the existing catch-all proxy works for new channels (no new routes).
- Push policy follows [LOCAL-MODE-AND-FORK](local-mode-and-fork-sprint.md): commit at every milestone, do **not** push until user reviews diff between milestones, then push as batch.
- Two forks ([Shepherd](../forks/internal-tools-fork/v0.1.0/scope.md) and [a tutoring fork](../forks/tutoring-fork/v0.1.0/scope.md)) are blocked on this sprint completing. Discord land = Shepherd unblocked; the framework alone = a tutoring fork's dashboard output routing unblocked.
- Three v6.2.0 docs ([event-driven-skills](../v6.2.0/event-driven-skills.md), [audit-log-and-analytics](../v6.2.0/audit-log-and-analytics.md), [google-workspace-mcp-integration](../v6.2.0/google-workspace-mcp-integration.md)) can start in parallel with this sprint — they hook agent-factory callbacks, not channel internals.
- Workshop critical path is unaffected — channels work is orthogonal to mcp-app-integrations (1.7), a2ui-workshop-demo (1.19), mcp-app-render-ux (1.26).
