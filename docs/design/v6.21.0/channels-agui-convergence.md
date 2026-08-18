# Channels AG-UI Convergence — one renderer, N transports

**Status**: Planned
**Priority**: P1 (unblocks an internal-tools fork's Discord UX; pays down the same tax as the frontend convergence doc)
**Estimated**: ~3 days (0.5 wire + 1 renderer + 1 A2UI projection + 0.5 identity)
**Scope**: Backend (`backend/channels/`) + CLI affordance
**Dependencies**: None hard. Shares the reducer-convergence *pattern* with [agui-event-consumption-convergence.md](../v6.7.0/agui-event-consumption-convergence.md) (frontend, Planned) — neither blocks the other.
**Created**: 2026-08-04
**Last Updated**: 2026-08-04

## Problem Statement

Channels consume the AG-UI event stream and throw almost all of it away. Every
adapter — Discord, Telegram, WhatsApp, email — runs the agent, keeps only
`TEXT_MESSAGE_CONTENT` deltas, concatenates them into one string, and posts it.
No streaming, no tool progress, no error surfacing, no A2UI.

**Current state (verified in code, not inferred):**

1. **Discord's streaming path is dead code.**
   [`discord.py:362`](../../../backend/channels/discord.py#L362) implements
   `send_streaming` properly — placeholder message, coalesced edits at
   `STREAM_EDIT_INTERVAL_SEC`, final atomic edit, chunking past the 2000-char
   limit. It is **never called.**
   [`base.py:230`](../../../backend/channels/base.py#L230) `_dispatch_inbound`
   routes unconditionally through `_invoke_skill` → `invoke_skill_collected` →
   `send()`. The only caller of `send_streaming` in the repo is
   `tests/channels/test_discord_streaming.py` — **7 green tests on unreachable
   code.**

2. **`supports_streaming` is declared by four adapters and read by zero.**
   `discord.py:97` sets it `True`; `base.py:98`, `telegram_.py:89`,
   `whatsapp.py:85`, `_demo_cli.py:46` set it `False`. Nothing branches on it.
   `base.py:274`'s docstring claims "Discord overrides `send` to stream with
   live message edits when the adapter sets `supports_streaming = True`" —
   that sentence describes behaviour that does not exist.

3. **The one tool-progress branch checks an event type we never emit.**
   [`discord.py:407`](../../../backend/channels/discord.py#L407) matches
   `event_type == "TOOL_CALL"`. The backend emits `TOOL_CALL_START`
   ([`adk/agui.py:268`](../../../backend/adk/agui.py#L268)), and the
   [AG-UI spec](https://docs.ag-ui.com/concepts/events) has no `TOOL_CALL`
   event at all — it defines `ToolCallStart` / `ToolCallArgs` / `ToolCallEnd` /
   `ToolCallResult` / `ToolCallChunk`. Dead code inside dead code: wiring
   step 1 alone would still show no tool progress.

4. **`RUN_ERROR` is handled by no channel.**
   [`_skill_invoke.py:75`](../../../backend/channels/_skill_invoke.py#L75)
   filters for `TEXT_MESSAGE_CONTENT` and drops everything else. A failed run
   produces no deltas, so `pieces` is empty and the user receives the string
   `"(no response)"`. A model outage, a tool crash, and a genuinely empty
   answer are indistinguishable on every channel. This is a standing violation
   of CLAUDE.md principle #8 (NEVER SILENT) in shipped code.

5. **Channel users have no identity beyond a UID.**
   [`_skill_invoke.py:94`](../../../backend/channels/_skill_invoke.py#L94)
   `_build_channel_user` returns
   `User(uid, email="", domain="", group_tags=frozenset())` with a
   `TODO(channels M2/M3)`. Channel-authed users therefore fail every
   domain-restricted or group-tagged skill — they match only public or
   owner-owned skills.

**What the backend actually emits** (`backend/adk/agui.py`): `RUN_STARTED`,
`RUN_FINISHED`, `RUN_ERROR`, `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`,
`TOOL_CALL_START`, and `CUSTOM` (carrying `STAGE_PROGRESS` and `A2UI_SURFACE`).
Channels consume exactly one of these seven.

**Impact:**
- **an internal-tools fork (blocker).** Discord is its primary surface. Today its users
  get a silent pause of unknown length followed by a wall of text, and cannot
  use any group-tagged skill.
- **Template.** Channels are a headline template feature; a forker who adds
  Slack inherits the same collect-then-dump ceiling and re-derives everything.
- **Every future channel.** Slack/Teams would each re-implement progress,
  errors and chunking from scratch.

## Goals

**Primary Goal:** Make AG-UI events and A2UI documents the channel-facing
contract, so an adapter is a thin renderer — three abstract methods plus a
small event→native projection — and any capability added once appears on
every channel.

**Success Metrics:**
- A Discord user sees a response begin streaming in **< 2s** and sees tool
  progress while it runs (today: nothing until the full answer lands).
- **Zero** AG-UI event types handled in an adapter-specific `if` outside the
  shared renderer.
- **100%** of channels render a visible message for `RUN_ERROR` (today: 0%).
- Adding a new channel requires **no** event-stream code — only native
  send/edit/embed primitives.

**Non-Goals:**
- Building the Slack or Teams adapter. This doc makes them cheap; it does not
  write them.
- Changing the backend event stream. It is already unified and correct.
- Interactive A2UI input on channels (buttons that feed data back to the
  agent). Read-only projection first; the surface-action loop is a later doc.
- Adopting the CopilotKit Channels SDK — see [Alternatives](#alternatives-considered).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Replaces batch-then-dump with live edits; first visible token in <2s on Discord. |
| 2 | EARNED TRUST | +1 | `RUN_ERROR` becomes a visible message instead of `"(no response)"` — failure stops masquerading as an empty answer. |
| 3 | SKILLS, NOT FEATURES | 0 | No change to the skill abstraction. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Untouched. |
| 5 | GRACEFUL DEGRADATION | +1 | Renderer degrades by design: a channel that cannot render a surface falls back to text; a channel that cannot edit falls back to collect-then-send. |
| 6 | PROTOCOL OVER CUSTOM | +1 | AG-UI + A2UI become the channel contract; no bespoke per-channel event vocabulary. Directly the axiom's case. |
| 7 | API FIRST | 0 | No new HTTP surface. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Tool calls and stage progress become visible in-channel; channel runs stop being opaque to the user. |
| 9 | SECURE BY CONSTRUCTION | 0 | Phase 4 opens a new access path (channel identity → group tags). Currently fail-closed; must stay server-side and verified — see [Security](#security-considerations). Net neutral, deliberately not claimed as a win. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The adapter becomes the thin client; the protocol carries the semantics. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Research: does any channel speak these protocols natively?

Checked before designing (step 5b/5c), because "the platform will give us this"
would change the design. **It will not.**

| Platform | Agent-UI story (Aug 2026) | AG-UI | A2UI |
|---|---|---|---|
| Slack | Proprietary and actively invested: Block Kit Tables, `task_card` blocks, streaming API. [MCP server + Real-Time Search API](https://slack.com/blog/news/mcp-real-time-search-api-now-available) GA'd early 2026 — *context access*, not UI. | No | No |
| Teams / M365 Copilot | [**MCP Apps GA June 2026**](https://devblogs.microsoft.com/microsoft365dev/mcp-apps-now-available-in-copilot-chat/) — interactive widgets returned with a tool result. Teams SDK also ships MCP + A2A. | No | No |
| Discord | Nothing protocol-level. Embeds, components, gateway. | No | No |
| Google Chat | [A2UI quickstart](https://developers.google.com/workspace/add-ons/chat/quickstart-a2ui-agent) exists — but Chat does **not** natively render A2UI; the Chat app translates A2UI JSON into Chat cards itself. A2UI is **Early Stage Public Preview**. | No | Via app-side mapping |

Two conclusions that shape this design:

1. **The mapping layer is ours to write regardless.** Google's own first-party
   channel does A2UI→cards by hand in a sample. CopilotKit's product *is*
   AG-UI→Block Kit. Nobody ships it for free. Writing it once against our own
   seam is strictly cheaper than writing it per channel — which is what we are
   doing today.
2. **Teams may not be a `BaseChannel` adapter at all.** MCP Apps is the only
   UI protocol a major channel has actually adopted, it is GA, and we already
   own that surface (`mcp-sandbox` Cloud Run service, `StaticArtefactFrame`,
   see [mcp-app-external-host-export.md](../v6.7.0/mcp-app-external-host-export.md)).
   Reaching Copilot chat by exposing our MCP server with MCP Apps widgets is
   plausibly far less code than Bot Framework + Azure AD registration. Flagged
   as an [open question](#open-questions), not resolved here.

## Design

### Overview

Extract the AG-UI event→presentation mapping out of `discord.py` into one
shared module, `backend/channels/_agui_render.py`. It consumes the raw event
stream and drives a small **channel primitive** interface that each adapter
implements natively. `_dispatch_inbound` picks streaming or collected based on
`supports_streaming` — the flag finally does something.

This is the same shape as the frontend's
[one-reducer-two-ingresses](../v6.7.0/agui-event-consumption-convergence.md)
convergence. Channels are the **third AG-UI consumer**; that doc says it
"GENERALIZES those patches so no future one is needed", and this is the future
one. Different runtime (Python/backend vs React/frontend), so the code is not
shared — the *contract* is: no consumer re-derives event semantics.

### The renderer

```
AG-UI event stream ──> AGUIChannelRenderer ──> ChannelSink (per-adapter)
                        (shared, one place)      begin() / update_text()
                                                 show_progress() / show_error()
                                                 render_surface() / finish()
```

`AGUIChannelRenderer` owns, once:

| Event | Renderer behaviour |
|---|---|
| `RUN_STARTED` | `sink.begin()` — placeholder / typing indicator |
| `TEXT_MESSAGE_CONTENT` | accumulate; `sink.update_text()` at most once per throttle interval |
| `TOOL_CALL_START` | `sink.show_progress(tool_name)` (the event we actually emit) |
| `CUSTOM` → `STAGE_PROGRESS` | `sink.show_progress(label)` |
| `CUSTOM` → `A2UI_SURFACE` | `sink.render_surface(doc)`, default fallback = text summary |
| `RUN_ERROR` | `sink.show_error(message, code)` — **never silent** |
| `RUN_FINISHED` | `sink.finish(full_text)` — final atomic write |

Throttling, accumulation, chunk-length limits and the empty-result case live in
the renderer, parameterised per adapter (`max_message_length`,
`edit_interval_sec`). Discord's existing coalescing logic is the prototype —
it moves, it is not rewritten.

`ChannelSink` has **default implementations for everything except
`update_text`**: `show_progress` no-ops, `render_surface` falls back to a text
summary, `show_error` posts the error as text. So a non-streaming adapter gets
error handling for free and opts into richness incrementally.

### Non-streaming adapters keep working

`invoke_skill_collected` is reimplemented on top of the renderer with a
`CollectingSink` — same collect-then-send behaviour, but it now sees
`RUN_ERROR` and returns a real message. Telegram, WhatsApp and email get error
surfacing with **zero adapter changes**.

### A2UI → Discord embeds

First real projection, proving the seam. A2UI Basic-catalog components map to
Discord's embed vocabulary (title/description/fields/footer/color); anything
unmappable degrades to formatted text rather than vanishing. Read the catalog
in the `agent-protocols` skill (`references/a2ui-v0.9-basic-catalog.md`) before
implementing — do not re-derive it.

Deliberately narrow: Discord only, read-only, Basic catalog only. It exists to
prove the sink boundary is in the right place before Slack's Block Kit lands
on top of it.

### Identity resolution (Phase 4)

**Corrected 2026-08-04 during sprint planning.** The first draft of this
section said `_build_channel_user` should read the advisory fields on
`channel_identities/{channel}_{user_id}` — including `group_tags` — into
`User`. That directly violates the invariant stated at
[`identity.py:22-24`](../../../backend/channels/identity.py#L22-L24):

> Group tags are stored advisory-only; the authoritative copy is the Firebase
> custom claim. **Channels do not grant privileges via group_tags** — only the
> `auth.firebase_auth.get_current_user` path does that.

The mirror is advisory precisely so a stale or tampered Firestore document
cannot grant access. Reading it into `User.group_tags` would make the mirror
authoritative by the back door.

**Corrected design:** after `IdentityResolver` resolves a channel user to a
`firebase_uid`, fetch that user's **authoritative custom claims by UID** via
the Firebase Admin SDK, then apply the same `_apply_derived_group_tags`
domain union that `get_current_user` uses
([`firebase_auth.py:117`](../../../backend/auth/firebase_auth.py#L117)). The
custom claim stays the single source of truth; channels gain no privilege
path of their own; the `channel_identities` mirror stays advisory and is used
only for display and debugging.

This is more work than the mirror read (an Admin SDK call per inbound
message, so it needs a short-TTL cache) and raises the estimate from ~0.5d to
~1d. It is the only version that preserves the invariant.

### CLI Surface

`aiplatform a2ui render` already renders a tool result through a registered
A2UI mapping. Extend it rather than adding a group:

```bash
aiplatform a2ui render --mapping compare_ppa --channel discord   # embed JSON projection
```

Plus the credential-free end-to-end seam: `_demo_cli.py` flips to
`supports_streaming = True` with a terminal sink, so
`python -m channels._demo_cli` exercises the full renderer path with no
Discord/Twilio/Mailgun credentials. It is the worked example in
[channels-adapter-howto.md](../../integrations/channels-adapter-howto.md), so
the howto gains the renderer section in the same PR.

## Implementation Plan

### Phase 1: Wire the dead path (~0.5 day)
- [ ] `_dispatch_inbound` branches on `supports_streaming` → `send_streaming` (~20 LOC)
- [ ] Fix `TOOL_CALL` → `TOOL_CALL_START` in `discord.py` (~2 LOC)
- [ ] `RUN_ERROR` handling in `invoke_skill_collected` — all channels (~15 LOC)
- [ ] Distinguish empty-result from error in the fallback text (~5 LOC)
- [ ] Revive the 7 tests in `test_discord_streaming.py` as integration-level, asserting reachability via `_dispatch_inbound` (~40 LOC)

### Phase 2: Extract the renderer (~1 day)
- [ ] `channels/_agui_render.py` — `AGUIChannelRenderer` + `ChannelSink` (~180 LOC)
- [ ] `CollectingSink`; reimplement `invoke_skill_collected` on it (~60 LOC)
- [ ] `DiscordSink`; `discord.py` sheds its inline event loop (~-80 / +60 LOC)
- [ ] `_demo_cli` terminal sink, `supports_streaming = True` (~30 LOC)
- [ ] **Drift guard**: a test asserting every event type in `adk/agui.py`'s emit set has renderer coverage — fails CI when a new event type is added without a channel decision (~50 LOC)

### Phase 3: A2UI → Discord embeds (~1 day)
- [ ] `A2UI_SURFACE` → Discord embed projection, Basic catalog (~120 LOC)
- [ ] Text-summary fallback for unmappable components (~40 LOC)
- [ ] `aiplatform a2ui render --channel discord` (~40 LOC)

### Phase 4: Channel identity (~0.5 day)
- [ ] `_build_channel_user` reads verified `channel_identities` advisory fields (~40 LOC)
- [ ] Tests for tagged-skill access via a channel identity, including the deny path (~60 LOC)

## Migration & Rollout

**Data model:** no schema change. `channel_identities/{channel}_{user_id}`
already carries the advisory fields; Phase 4 reads what is written.

**Feature flags:** none. Phases 1-3 are strictly additive to user-visible
behaviour (silence → visible progress). Phase 4 changes an access decision and
lands behind `CHANNEL_IDENTITY_ENRICHMENT=1` until the deny-path tests are
green in dev.

**Rollback:** Phase 1 reverts by flipping `supports_streaming = False` on
Discord — one line, no data migration. Phases 2-3 revert by commit.

**Environment variables:** `CHANNEL_IDENTITY_ENRICHMENT` (Phase 4, dev first).

## Testing Strategy

### Backend Tests (pytest)
- [ ] Renderer unit tests: each event type → expected sink calls
- [ ] Throttling: N deltas inside one interval → 1 `update_text`
- [ ] `RUN_ERROR` → `show_error` on **every** registered sink, including collecting
- [ ] Empty result vs error produce different user-visible text
- [ ] Chunking at `max_message_length` boundaries (Discord 2000)
- [ ] A2UI Basic-catalog → embed projection; unmappable → text fallback
- [ ] Drift guard (Phase 2) — emitted event set vs renderer coverage
- [ ] Phase 4: tagged skill allowed with enriched identity, denied without

### Manual Testing
- [ ] `python -m channels._demo_cli` — text streams incrementally, tool progress shows, an induced error prints
- [ ] **Real Discord guild** (non-negotiable): send a tool-using prompt, watch the placeholder edit live, watch tool progress, confirm a >2000-char answer chunks correctly
- [ ] Induce a model failure on Discord — a visible error arrives, not `"(no response)"`

> Unit tests passing does **not** mean it renders. This is the channel analogue
> of the A2UI-workspace verification rule in CLAUDE.md: the Discord run against
> a real guild is required before this doc moves to `implemented/`.

## Security Considerations

**Phase 4 is the only part with a security surface, and it is the sharp one.**
Today channel users are fail-closed: no email, no domain, no group tags,
therefore no access to restricted skills. Phase 4 opens that path, so:

- Group tags come **only** from the Firebase custom claim, fetched by UID via
  the Admin SDK, unioned with `clients/{domain}.derived_group_tags` — the same
  authority `get_current_user` uses. **Never** from the `channel_identities`
  mirror (advisory by design, see `identity.py:22-24`) and **never** from
  channel-supplied data: a Discord nickname, guild role or display name must
  not grant access.
- The `channel_identities` record is used only to resolve UID, and for
  display/debugging. Its `group_tags` field stays advisory; a test must assert
  that tampering with it grants nothing.
- The channel's own trust check stays the gate: Ed25519 webhook signature or
  authenticated gateway. Enrichment happens strictly after that.
- Failure to resolve stays **fail-closed** — an unenriched user keeps today's
  restricted `User`, never a permissive default.
- Group-tagged skills reach customer-confidential content (CLAUDE.md security
  rule). A Discord guild is a **weaker trust boundary** than Firebase auth:
  guild membership is not employment. Per-guild allowlisting via
  `channel_routes/discord/{guild_id}` must remain mandatory for any
  tag-granting identity — document that explicitly rather than assuming it.

Phases 1-3 change presentation only. One caveat worth stating: A2UI surfaces
rendered into a channel put derived content into a third-party platform's
message history. For confidential-content skills, that is a deliberate decision
per deployment, not a default — the same reasoning as the thumbnail rule.

## Performance Considerations

- **Discord edit rate limits** are the binding constraint. `STREAM_EDIT_INTERVAL_SEC`
  coalescing already exists and moves into the renderer unchanged; the drift
  guard must not tempt anyone into per-delta edits.
- The Discord gateway still requires `min_instances=1`. Unchanged.
- Renderer overhead is one dict dispatch per event — negligible against network.
- Streaming **reduces** perceived latency without changing token cost.

## Success Criteria

- [ ] `supports_streaming` is read by runtime code; Discord streams live in a real guild
- [ ] No adapter contains an AG-UI event-type conditional outside `_agui_render.py`
- [ ] Every channel renders a visible message on `RUN_ERROR`
- [ ] `test_discord_streaming.py` tests exercise a reachable path
- [ ] Drift-guard test present and green
- [ ] A2UI surface renders as a Discord embed; unmappable components degrade to text
- [ ] `python -m channels._demo_cli` demonstrates the full path with no credentials
- [ ] `aiplatform a2ui render --channel discord` works
- [ ] Phase 4: a channel user reaches a group-tagged skill; unenriched users still denied
- [ ] `make lint && make test-fast` clean

## Alternatives Considered

**CopilotKit Channels SDK** ([github.com/CopilotKit/channels-sdk](https://github.com/CopilotKit/channels-sdk), MIT).
Works with any AG-UI-compatible agent and explicitly lists Google ADK, so we
are protocol-compatible. Rejected on three grounds: it is Node.js 22+ against
our Python channel layer; channel ingress/egress routes through **CopilotKit
Intelligence**, a hosted intermediary (self-hosting is enterprise/sales-gated),
which puts a third party in the message path for content covered by the
CLAUDE.md security rule and the GCP-project-edge privacy boundary; and Discord
is not shipped there anyway — their docs list Slack GA, Teams as a controlled
integration, and Discord/WhatsApp/Telegram as "coming soon, via direct SDK
adapters", i.e. the approach we already took. **Worth reading** their Slack
adapter as a reference for the AG-UI→Block Kit mapping when Slack lands; MIT
licence permits it.

**Per-channel bespoke handling (status quo).** Rejected: it is the thing that
produced dead code in `discord.py` and would multiply by channel count.

## Open Questions

- **Is Teams an MCP Apps surface rather than a `BaseChannel` adapter?** MCP
  Apps is GA in M365 Copilot and we already own that surface. Deserves a spike
  before anyone writes `teams.py`.
- **Does Slack's `task_card` block map cleanly onto `show_progress`?** If yes,
  the sink interface is validated by a second channel before we commit to it.
- **Should `A2UI_SURFACE` render inline or as a link back to the web
  workbench?** For confidential-content skills a link may be the correct
  default — but that trades against principle #8 if the link is the only
  feedback.
- **Interactive A2UI on channels** (buttons feeding the surface-action loop)
  is deferred. Discord components and Block Kit both support it; the question
  is whether the action-run path can be driven from a channel at all.

## Related Documents

- [agui-event-consumption-convergence.md](../v6.7.0/agui-event-consumption-convergence.md) — the frontend twin; same pattern, different runtime
- [channels.md](../v6.1.0/implemented/channels.md) — the framework this extends
- [discord-channel.md](../v6.1.0/implemented/discord-channel.md) — the adapter carrying the dead streaming code
- [channels-adapter-howto.md](../../integrations/channels-adapter-howto.md) — gains a renderer section
- [mcp-app-external-host-export.md](../v6.7.0/mcp-app-external-host-export.md) — relevant to the Teams question
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI conventions
- [AG-UI event spec](https://docs.ag-ui.com/concepts/events) · [A2UI](https://a2ui.org/) · [MCP Apps in M365 Copilot](https://devblogs.microsoft.com/microsoft365dev/mcp-apps-now-available-in-copilot-chat/)
