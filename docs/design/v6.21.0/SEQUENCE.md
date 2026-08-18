# v6.21.0 — Build Sequence

Channels. Opened 2026-08-04 after a question about the CopilotKit Channels SDK
turned into a code audit, and the audit found the Discord adapter's streaming
path has never executed.

One doc so far. It is a **paydown plus a seam**, not a new feature: the
capability was written in May 2026 (Discord adapter, 39 tests, sprint evaluator
PASS 92/100) and has been unreachable ever since, because nothing calls
`send_streaming` and nothing reads `supports_streaming`. The seam half — one
shared AG-UI renderer behind a per-adapter sink — is what makes Slack and Teams
cheap later, and what stops the next adapter re-deriving progress, chunking and
error handling for a third time.

The research that opened the doc is worth carrying forward on its own: **no chat
platform natively renders AG-UI or A2UI**, and none is on a path to. Slack is
investing hard in proprietary agent primitives (Block Kit Tables, `task_card`,
streaming API); Teams shipped **MCP Apps GA in June 2026**; Google's own Chat
A2UI quickstart does the A2UI→cards mapping in the app, by hand. The mapping
layer is ours to write on every channel regardless of vendor — so it should
exist once, in one module, rather than per adapter.

That research also opens a question this version does not answer: whether Teams
is a `BaseChannel` adapter at all, or an MCP Apps surface on infrastructure we
already run (`mcp-sandbox`, `StaticArtefactFrame`). Flagged, not resolved — it
deserves a spike before anyone writes `teams.py`.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [channels-agui-convergence](channels-agui-convergence.md) | P1 | ~3d (0.5 + 1 + 1 + 0.5) | None hard. Shares a *pattern*, not code, with [agui-event-consumption-convergence](../v6.7.0/agui-event-consumption-convergence.md) (frontend, Planned) — neither blocks the other | **the internal-tools fork-fork blocker.** Four verified defects in shipped code: `send_streaming` unreachable (`base.py:230` always collects); `supports_streaming` declared by 4 adapters, read by 0; the sole tool-progress branch matches `"TOOL_CALL"`, an event the AG-UI spec doesn't define and we never emit (`adk/agui.py:268` emits `TOOL_CALL_START`) — dead code inside dead code; `RUN_ERROR` dropped on every channel, so a model outage and an empty answer both render `"(no response)"` — a standing CLAUDE.md #8 violation. Net axiom score **+6**, no conflicts. Phase 4 (channel identity → group tags) is the only security-relevant part and lands flag-gated. Acceptance requires a **real Discord guild** run — unit tests passing is the same class of evidence that let this ship dead. |

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 1 | Wire the dead path: `supports_streaming` branch, `TOOL_CALL_START` fix, `RUN_ERROR` on all channels, revive the 7 orphaned tests against a reachable path | ~0.5d | Planned |
| 2 | Extract `channels/_agui_render.py` — `AGUIChannelRenderer` + `ChannelSink`; collecting/Discord/terminal sinks; **drift guard** vs the emitted event set | ~1d | Planned |
| 3 | A2UI → Discord embeds (Basic catalog, read-only) + `aiplatform a2ui render --channel discord` | ~1d | Planned |
| 4 | Channel identity enrichment — `channel_identities` advisory fields → `User.group_tags`, behind `CHANNEL_IDENTITY_ENRICHMENT` | ~0.5d | Planned |

## What ships in v6.21.0

- **Discord stops going silent.** A response begins streaming in under 2s and
  shows tool progress while it runs, instead of an unbounded pause followed by
  a wall of text. The code for this already exists and has never run.
- **Every channel surfaces failure.** `RUN_ERROR` renders a visible message on
  Discord, Telegram, WhatsApp and email — via the collecting path, so three of
  those four need no adapter change. Today all four silently print
  `"(no response)"` when a run fails, which is indistinguishable from an empty
  answer.
- **A renderer seam, so the next channel is a renderer.** One module owns
  AG-UI event semantics for all channels; an adapter implements native
  send/edit/embed primitives and inherits throttling, chunking, progress and
  error handling. `ChannelSink` defaults mean a minimal adapter gets error
  surfacing for free.
- **A drift guard.** A test that fails CI when a new AG-UI event type is
  emitted without a channel decision — the mechanism that stops this doc from
  needing a sequel, and the same idea as the frontend convergence doc's.
- **A credential-free verification path.** `python -m channels._demo_cli`
  exercises the full renderer with no Discord/Twilio/Mailgun credentials, and
  stays the worked example in the adapter howto.
- **A2UI reaching a channel for the first time** — Discord embeds, Basic
  catalog, read-only, with text fallback for anything unmappable. Deliberately
  narrow: it exists to prove the sink boundary sits in the right place before
  Block Kit lands on it.
- **Not shipping:** Slack, Teams, interactive A2UI on channels, or the
  CopilotKit Channels SDK (rejected — Node runtime, a hosted intermediary in
  the message path for confidential content, and Discord isn't in it anyway;
  see the doc's Alternatives section).

## Dependency graph

```
Phase 1 — wire the dead path ─► useful alone; ships the streaming Discord
      │                         adapter we already paid for
      │
      └─► Phase 2 — extract the renderer + drift guard
                │      (Phase 1's fixes move into it rather than being rewritten)
                │
                ├─► Phase 3 — A2UI → Discord embeds ─► proves the sink boundary
                │                                       before Slack/Block Kit
                │
                └─► (later, separate docs) Slack adapter = a sink
                                           Teams = OPEN: adapter, or MCP Apps surface?

Phase 4 — channel identity  ─► independent of 1–3; gated by
                               CHANNEL_IDENTITY_ENRICHMENT; the only part
                               that changes an access decision
```
