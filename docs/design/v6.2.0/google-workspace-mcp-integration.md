# Google Workspace MCP Integration Guide

**Status**: Planned — surfaced by [Shepherd / an internal-tools fork](../forks/internal-tools-fork/v0.1.0/scope.md)
**Priority**: P2 — documentation + reference config, not a feature build
**Scope**: Template documentation + one reference skill + auth pattern decisions
**Dependencies**: [MCP strategy](../v6.0.0/implemented/agent-factory.md) (template already supports `McpToolset`)
**Created**: 2026-05-16

## Problem Statement

Google released a catalogue of hosted Workspace MCP servers (Drive, Gmail, Calendar, Sheets, etc.) in early May 2026. The template already supports remote MCP servers via `McpToolset`, but the auth pattern (OAuth-per-user vs service-account-with-domain-wide-delegation) is non-obvious, and folder/scope configuration differs per server.

Without documentation, every fork that wants Drive search or Gmail integration will:
- Pick the wrong auth flow for their use case (e.g., OAuth-per-user for a scheduled scan, which fails when no user is online to refresh the token)
- Forget to scope the server's access (giving full Drive access when only one folder was intended)
- Hit rate limits unexpectedly because Google's quotas aren't documented per-server

This doc captures the auth + scoping + quota patterns so the next fork doesn't re-derive them.

## Goals

**Primary:** Template ships a `docs/integrations/google-workspace.md` guide + one reference skill (`drive-search`) demonstrating the recommended pattern, so any fork can wire Workspace MCP in <1h.

**Success Metrics:**
- A fork wires Drive search via Google MCP in <1h elapsed (config + auth + scope), not via building a bespoke server
- Auth pattern decision is unambiguous given the skill's trigger type (user-facing vs scheduled vs system)
- Folder scoping is enforced at skill config, not relying on Google MCP's defaults
- Quota awareness: skill author knows the per-server rate limit before they ship

**Non-Goals:**
- Building any MCP server (we're using Google's hosted ones)
- Supporting non-Google providers' Workspace alternatives (Microsoft Graph etc.) — separate doc when a use case appears
- Custom Drive scanning logic for periodic indexing — defer to v0.2.0 if Google's MCP search proves insufficient

## Design

### Survey of Google-hosted Workspace MCP servers

**TODO during implementation, day 1:** verify the current catalogue. As of 2026-05-16 (early-release window):

| Server | Capability | Probable auth pattern |
|--------|------------|----------------------|
| `drive-mcp` | Search + read documents in Drive | OAuth (user-facing) or SA+DWD (system) |
| `gmail-mcp` | Read inbox, send messages | OAuth (user-facing); SA+DWD for digests |
| `calendar-mcp` | Read events, create meetings | OAuth (user-facing) |
| `sheets-mcp` | Read + write Google Sheets | OAuth or SA depending on sheet ownership |

**Catalogue churn risk:** Google's MCP server lineup will change between now and any given fork's start date. The reference skill below uses environment-config + Firestore-config for server URLs, so swapping is a config change.

### Auth pattern decision tree

```
Q1: Is the skill user-facing (a Sheep asks it a question)?
    YES → OAuth-per-user flow. User signs into Google during first use.
            Token refresh handled by Workspace MCP server.
            Pro: respects user's own Drive access.
            Con: every user must sign in once.
    NO → Q2.

Q2: Is the skill scheduled or event-triggered?
    YES → Service account with domain-wide delegation.
            fork admin grants DWD via Google Workspace admin console.
            SA acts as a designated bot identity ("shepherd-bot@the internal-tools fork.org").
            Pro: works without a user online.
            Con: requires admin setup; bot identity sees what bot identity is given.
    NO → impossible — skills are either user-driven or trigger-driven.

Q3: Does the skill take a write action (send email, create event, modify sheet)?
    YES → MUST be OAuth with explicit user consent per write.
            Even if a scheduled scan is involved, the write action is OAuth-flow.
            Reason: defense in depth + auditability.
```

**Shepherd-specific:**
- `contract-qa` (user-facing) → OAuth-per-Sheep on first use, scope = `drive.readonly`, folder filter = configured contract folders
- `contract-watch` (scheduled) → SA+DWD as `shepherd-bot@the internal-tools fork.org`, scope = same folder set, read-only

### Folder/scope enforcement

Google MCP servers expose the user's/SA's full Workspace by default. Scope at *two* layers:

1. **OAuth scope or SA permission** — `drive.readonly` for Q&A skills; never `drive` (full)
2. **Skill-config folder filter** — `LessonConfig.drive_folders: list[str]` (or equivalent in `SkillConfig`). The skill's tool-call wraps Drive search with `parents in [folder_ids]` filter.

Don't trust scope-at-server-level alone; the skill must filter.

### McpToolset wiring example

Template ships a reference skill `drive-search` showing the wiring:

```python
# backend/skills/templates/drive-search.yaml
slug: drive-search
title: "Drive Search"
prompt: |
    You can search the team's Drive. Use the `drive_search` tool to find files.
    Always cite the file URL in your response. Folder scope is enforced — files
    outside the configured folders are not visible to you.
trigger:
    type: request_response
tools:
    - type: mcp
      server_url: $DRIVE_MCP_URL
      auth: oauth_per_user
      scope_filter:
          drive_folders: ${SKILL_DRIVE_FOLDERS}
```

The agent factory translates this into an `McpToolset` instance at agent construction time. OAuth tokens are stored per-user in Firestore (encrypted via Cloud KMS).

### Rate limits + quotas

Document per-server quotas in the integration guide:
- Drive Search API: 1000 requests/100s per user (OAuth) or 10k/100s per project (SA)
- Gmail Send: 100 emails/day per user (OAuth); higher with Workspace business plan
- Sheets read: 60 requests/min per user

Skills hitting these limits should:
- Cache server results in Firestore with a TTL (e.g., contract list cache for 1h)
- Use scheduled batched reads instead of per-question API calls
- Fall back gracefully ("I can't check Drive right now, try again in a minute")

The reference skill includes a 5-minute result cache pattern.

## Implementation Plan

~2h total — mostly documentation + one config example.

| Step | Est | Notes |
|------|-----|-------|
| Survey current Google MCP server catalogue (day-1 task before Shepherd Chunk 2) | 1h | Verify URLs, auth options, scope params |
| Write `docs/integrations/google-workspace.md` with auth decision tree + quota table | 0.5h | Lift from this doc |
| Reference skill `drive-search.yaml` + agent factory wiring example | 0.5h | Template ships this |

Plus follow-up if Shepherd's Chunk 2 hits limits:
- 4h fallback: bespoke `drive-contracts` MCP server build, becomes the alt-pattern documented for forks that need it

## Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Google catalogue changes server names/URLs | High | Config-driven (env + Firestore); doc explicitly says "verify catalogue on day 1 of any fork" |
| OAuth-per-user adds onboarding friction Sheep don't tolerate | Medium | For Shepherd, fall back to SA+DWD for all read skills; OAuth only for write actions |
| Server lacks folder-scoping in API | Medium | Skill-side filter is the belt-and-braces enforcement; works regardless |
| Rate limits surface only at scale | Medium | Documented quotas + caching pattern in reference skill; alert on `429` rate in audit log |
| Tokens stored in Firestore become a target | Medium | Cloud KMS encryption + access via SA only + 90-day rotation |

## Testing Strategy

- Manual integration: Sheep signs in via OAuth, runs `drive-search`, verifies folder scope is enforced (file outside scope is invisible)
- Adversarial: configure folder filter, attempt search with `parents:` filter overridden — confirm skill rejects (filter is enforced server-side via the skill's tool wrapper, not just the prompt)
- SA path: trigger a scheduled skill, verify it runs without user login
- Quota: simulate burst → confirm cache hits prevent quota exhaustion

## Security Considerations

- OAuth tokens encrypted at rest (Cloud KMS) + scoped to least privilege (`drive.readonly` etc.)
- SA+DWD restricted to a designated bot identity per fork; DWD scopes itemised and reviewed in Workspace admin console
- Folder filter applied at skill tool-call layer (defense in depth — server scope + skill filter)
- Token refresh failures logged but never silently retried with elevated scope
- Audit log captures every Google MCP call: skill, tool, file IDs touched (but NOT file contents)

## Open Questions

1. **Token storage location.** Firestore + KMS, or Secret Manager? Firestore is per-user friendly; Secret Manager is more standard for service-level secrets. Recommendation: Firestore for user OAuth, Secret Manager for SA credentials.
2. **Multi-Workspace forks.** A fork serving multiple Workspaces (rare but possible) needs per-tenant SA configuration. Defer until a use case appears.
3. **Drive comments, sharing changes — eventarc?** Google offers Workspace Events API; could feed events into [event-driven-skills](event-driven-skills.md). Defer to v0.2.0 unless Shepherd's contract-watch needs change-feed instead of polling.
4. **Caching layer location.** Per-skill cache in Firestore, or shared cache in Cloud Memorystore? Firestore is simpler; revisit if cache pressure becomes real.

## Related Documents

- [Event-driven skills](event-driven-skills.md) — pairs with this for scheduled Drive scans
- [Audit log + analytics](audit-log-and-analytics.md) — captures Google MCP tool calls
- [an internal-tools fork scope](../forks/internal-tools-fork/v0.1.0/scope.md) — first consumer (contract-qa + contract-watch)
- [Agent factory](../v6.0.0/implemented/agent-factory.md) — `McpToolset` wiring point
- [MCP strategy](../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/project_mcp_strategy.md) — template MCP approach: FunctionTool for core, McpToolset for remote
