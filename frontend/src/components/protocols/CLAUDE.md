# CLAUDE.md — `frontend/src/components/protocols/` A2UI render side

This is the **render** half of A2UI. The **emission** half (where things
usually break) is backend — see `backend/adk/CLAUDE.md` and the memory
`a2ui-workspace-render-trap`. Read those first when a surface "won't render."

## Rules

- **No bespoke per-tool React.** Every tool result / surface renders through the
  generic `A2UISurfaceMount` (→ `@a2ui/react`). If you're about to write a
  component keyed off a specific tool name, STOP and register a backend
  result→A2UI mapping instead (`backend/adk/a2ui_result_render.py`).
- **Surfaces flow: backend `A2UI_SURFACE` CUSTOM event → `WorkspaceA2uiEventRouter`
  → `SurfaceRegistry.appendMessages(surfaceId, …)`.** The router MUST run inside
  `SurfaceRegistryProvider` (it reads the registry). `appendMessages`
  auto-synthesizes `createSurface`, so a mount isn't required for the surface
  state to exist.
- **Tabs & index come from `artifact` metadata.** `useArtifacts()` lists
  surfaces that have BOTH a live surface AND `artifact` metadata; each becomes a
  workbench Result tab (7.5) and a row in the Workspace/Home index
  (`WorkbenchHome` / `WorkbenchIndex`). `placement: "chat"` renders inline in the
  transcript instead (elicitation forms), NOT as a tab.
- **Auto-focus new workbench elements** (repo principle #7) — a new artifact
  surface focuses its tab; a dominant `workspace` surface focuses the Workspace
  tab. Don't just badge.

## Verify in a real browser

A surface passing jsdom tests can still render blank live (grounding-redirect
URLs, a missing catalog component, the surface never registering). Confirm with
the `aitana-frontend-verify` skill (chrome-devtools MCP) — rendered DOM +
the `A2UI_SURFACE` event on the network/SSE stream — before calling it done.
