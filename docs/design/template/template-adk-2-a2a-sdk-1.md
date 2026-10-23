# ADK 2 upgrade · 05 — a2a-sdk 0.3 → 1.x

**Status**: Proposed
**Phase**: 1, after 03b
**Estimated**: 1–2 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-16

## Why

- ADK ≥2.6 allows a2a-sdk 1.x (changelog line 1402) and declares it with `[http-server]` (839). Our `a2a-sdk<1.0.0` pin still resolves on 2.12, but it holds us on a line that is getting fixes only (314).
- New A2A features we want sit on 1.x:
  - `RemoteA2aAgent` task mode + auth (2.8)
  - per-call agent-card auth headers and serving under a path prefix (2.6)
  - `context_builder` (2.10)
  - persistent task stores (1.34)
  - an `A2aAgentExecutor` factory in `to_a2a` (1.34)

## Touch points

These use 0.3-style imports from `a2a.server.*` and `a2a.types`:
- `backend/protocols/a2a.py`: `A2AStarletteApplication`, `DefaultRequestHandler`, `InMemoryTaskStore`, `InMemoryPushNotificationConfigStore`, `AgentCard`
- `backend/protocols/a2a_invocation.py`: `A2aAgentExecutor`, `RequestContext`
- `backend/protocols/a2a_client.py`
- `backend/protocols/file_extraction.py`: `FilePart`, `FileWithBytes`, `FileWithUri`, `Part`, `TextPart`

## Plan

1. Map each import to its 1.x location; follow the a2a-sdk 1.0 migration notes. Keep the changes in these four files.
2. Keep `force_new_version=True` on `A2aAgentExecutor` (template item G46). Gemini Enterprise doesn't send the extensions hint. Check whether 1.x / ADK 2.x changed the default or the flag's name.
3. Re-run the template's A2A gates: card spec compliance (G43), invocation via Next ingress (G45), and file-aware traffic (G46). Use `make verify-a2a`.
4. **Optional, separate PR:** replace the in-memory task store with a persistent one. This only matters if A2A tasks must survive instance recycling.

## Exit

- `make verify-a2a` green.
- The Gemini Enterprise registration still validates.
- A peer agent sending a file gets it through extraction.
