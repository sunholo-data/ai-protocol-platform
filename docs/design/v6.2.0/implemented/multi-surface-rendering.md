# Multi-Surface A2UI Rendering

**Status**: ✅ Implemented 2026-05-18 — all 5 milestones shipped (M1 backend schema + M2 SurfaceRegistry + M3 routing + M4 patch semantics + M5 workshop demo + howto)
**Priority**: P1 — workshop-strengthening; first commercial fork needs it; template-eligible
**Scope**: Backend A2UI tool-call schema + frontend `SurfaceRegistry` + per-surface persistence + W6 workshop demo upgrade
**Dependencies**: [A2UI tool delivery (1.0 ✅)](../../v6.1.0/implemented/a2ui-tool-delivery.md), [chat-message-rendering (1.1 ✅)](../../v6.1.0/implemented/chat-message-rendering.md), [document workspace (1.10 ✅)](../../v6.1.0/document-ui.md) — the layout shell we extend
**Created**: 2026-05-18
**Operating manual:** [docs/integrations/multi-surface-rendering.md](../../../integrations/multi-surface-rendering.md) — skill-author howto
**Implementation note:** M3 chose a REGISTRY-DRIVEN dispatch pattern over the design's `createPortal`-from-bubble. The portal-from-bubble approach has a race when chat bubbles unmount on scroll/virtualization; the registry-driven path keeps surface trees alive across chat re-renders and makes M4's patch semantics natural (registry owns the tree, components reused by reference for identity-preserving patches).

## Problem Statement

A2UI ([a2ui.org](https://a2ui.org)) treats a *surface* as a first-class concept: "a canvas for components (dialog, sidebar, main view) with its own component tree and data model." Specs are delivered with a `surfaceId` that names where they should mount. Updates flow per-surface, independently.

The v6 template's renderer does not honour this. Today (verified 2026-05-18 against `frontend/src/components/protocols/A2UIRenderer.tsx` + `MessageBubble.tsx:106`):

- `A2UIRenderer` accepts `spec`, `onAction` — no `surfaceId` prop
- A2UI specs always render **inline inside the chat MessageBubble** that produced the tool call
- The `send_a2ui_json_to_client` tool-call payload on the backend is `{root, components, data}` — no surface field
- The chat page layout *does* have physical regions (sidebar `w-64`, document panel `w-1/2`, chat `flex-1`) — but they are static layout, not addressable by the agent
- Grep across `frontend/src/` and `backend/` finds zero hits on `surfaceId`, `surface_id`, `targetSurface`

AIPLA's UX design (ADR-015) needs four named surfaces:

| Surface | Purpose | Lifecycle |
|---------|---------|-----------|
| `chat` | Conversational signal — user input, transient agent text, acknowledgements | Turn-scoped (current behaviour) |
| `workspace` | Primary work area — live dashboards, open artefacts, search results | Persistent across turns; this is where the teacher's eyes mostly live |
| `sidebar` | Persistent context — current class, current group, navigation | Persistent across turns + sessions |
| `modal` | Focused blocking tasks — artefact approval, confirmations | Auto-dismiss on action |

Without surface routing, the only way to expose a class-status dashboard is to render it inline in a chat bubble. New chat messages then push the dashboard up and out of view. The agent's "I directed you to look here" becomes "scroll the chat history to find what you need" — the exact failure ADR-015 was written to prevent.

## Goals

**Primary:** Skills can declare a target surface; the agent's tool calls route there; the chat surface remains the default for skills that don't opt in.

**Success Metrics:**
- A skill setting `surface_id="workspace"` lands its A2UI tree in the workspace mount, NOT in the chat bubble
- `dataModelUpdate` events patch the existing workspace tree (no full re-render; component identity stable by `componentId`)
- Workspace + sidebar + modal surfaces persist across turns within a session; chat remains turn-scoped (back-compat)
- The W6 workshop demo gets a "show me Munich → workspace renders map → zoom to old town → workspace patches in place" flow, demonstrating the *protocol* not just the renderer
- Existing skills (no `surface_id`) continue rendering inline in chat — zero migration cost
- A fork can declare new surface mount points (`teacher-grid`, `student-feed`) via the same `SurfaceRegistry` API

**Non-Goals:**
- Layout authoring DSL — surfaces are mount points declared in React, not data-driven
- Multi-window / multi-device surfaces (one device, one layout)
- Co-editing across surfaces by multiple users — single-user session is the assumption
- Replacing the document workspace's `BlocksRenderer` — that's a separate pipeline for parsed documents; A2UI surfaces are agent-directed UI

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | `dataModelUpdate` patches avoid full re-render — feels live |
| 2 | EARNED TRUST | +1 | Agent's "I put it there" matches the user seeing it there + staying there |
| 3 | SKILLS, NOT FEATURES | +1 | Skills declare their target surface; the framework routes |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal |
| 5 | GRACEFUL DEGRADATION | +1 | Skills without `surface_id` keep working inline-in-chat |
| 6 | PROTOCOL OVER CUSTOM | +2 | **Adopts A2UI's native `surfaceId` semantics — the protocol designed exactly this pattern** |
| 7 | API FIRST | +1 | `surface_id` is a tool-call payload field; same API for chat, workshop, fork |
| 8 | OBSERVABLE BY DEFAULT | 0 | Audit log captures the surface targeted (`channel`-style metadata) once [audit-log-and-analytics](audit-log-and-analytics.md) ships |
| 9 | SECURE BY CONSTRUCTION | +1 | Per-surface allowlist (e.g., `modal` requires user-initiated trigger) defends against agent-induced UI hijack |
| 10 | THIN CLIENT, FAT PROTOCOL | +2 | **Layout is just N React mount points; agent decides what goes where via the protocol** |
| | **Net Score** | **+10** | Threshold: >= +4 |

## Design

### Backend schema addition

A2UI tool calls (`send_a2ui_json_to_client`) get one optional new field:

```python
# backend/tools/a2ui_tool.py (or wherever the tool definition lives)
class A2UIDelivery(BaseModel):
    root: str
    components: list[A2UIComponent]
    data: dict[str, Any] = Field(default_factory=dict)
    surface_id: Literal["chat", "workspace", "sidebar", "modal"] | str | None = None
    update_mode: Literal["replace", "patch"] = "replace"
```

`surface_id=None` (or unset) renders inline in chat — the existing path, fully backwards compatible. `update_mode="patch"` carries `dataModelUpdate` semantics and requires the target surface to already have an active tree.

Skills declare their default surface in `SkillConfig.tool_configs.a2ui.default_surface` (optional). The tool call can override per-invocation.

### Frontend `SurfaceRegistry`

A new React context that maps `surfaceId → React mount point`. The app layout (or fork-specific layouts) registers mount points; `A2UIRenderer` reads the registry and uses `createPortal` to deliver specs.

```typescript
// frontend/src/providers/SurfaceRegistry.tsx (new)
interface Surface {
  id: string;
  mountRef: RefObject<HTMLDivElement>;
  policy: SurfacePolicy;  // see below
}

interface SurfacePolicy {
  persistence: "turn-scoped" | "session-scoped" | "indefinite";
  // turn-scoped:    chat default; cleared after RUN_FINISHED if not patched
  // session-scoped: workspace/sidebar default; cleared on session change
  // indefinite:     sidebar can opt into this for cross-session context (admin allowlist)

  acceptsPatches: boolean;        // does this surface honour update_mode="patch"?
  requiresUserGesture: boolean;   // modal requires user-initiated; agent can't pop one unbidden
}

const DEFAULT_SURFACES: Record<string, SurfacePolicy> = {
  chat:      { persistence: "turn-scoped",    acceptsPatches: false, requiresUserGesture: false },
  workspace: { persistence: "session-scoped", acceptsPatches: true,  requiresUserGesture: false },
  sidebar:   { persistence: "session-scoped", acceptsPatches: true,  requiresUserGesture: false },
  modal:     { persistence: "turn-scoped",    acceptsPatches: false, requiresUserGesture: true  },
};
```

### Frontend `A2UISurfaceMount`

Components declare a named mount point in the layout tree. The registry tracks which surfaces have an active mount; specs targeting an unmounted surface fall back to chat with a warning.

```typescript
// frontend/src/components/protocols/A2UISurfaceMount.tsx (new)
export function A2UISurfaceMount({
  surfaceId,
  policy,
  className,
}: {
  surfaceId: string;
  policy?: Partial<SurfacePolicy>;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const registry = useSurfaceRegistry();
  useEffect(() => {
    registry.register(surfaceId, ref, policy);
    return () => registry.unregister(surfaceId);
  }, [surfaceId, policy, registry]);
  return <div ref={ref} className={className} data-surface={surfaceId} />;
}
```

Usage in the chat page layout:

```typescript
// frontend/src/app/chat/[...path]/page.tsx (illustrative)
<aside className="w-64">
  <A2UISurfaceMount surfaceId="sidebar" />
</aside>
<div className="w-1/2">
  <A2UISurfaceMount surfaceId="workspace" />
</div>
<main className="flex-1">
  <ChatMessageList />   {/* A2UI specs with surface_id=chat (or unset) render inline as today */}
</main>
<A2UISurfaceMount surfaceId="modal" />  {/* off-canvas, only mounts when active */}
```

### Updated `A2UIRenderer`

The renderer reads `surfaceId` from the tool-call payload + uses the registry to portal:

```typescript
export function A2UIRenderer({ spec, surfaceId, updateMode, onAction }: A2UIRendererProps) {
  const registry = useSurfaceRegistry();
  const effectiveSurface = surfaceId ?? "chat";

  // chat surface: render inline as today (backwards compat)
  if (effectiveSurface === "chat") {
    return <A2UIViewer ... />;
  }

  // other surfaces: portal into the registered mount, or fall back to chat with warning
  const mount = registry.getMount(effectiveSurface);
  if (!mount?.current) {
    if (process.env.NODE_ENV !== "production") {
      console.warn(`A2UI surface "${effectiveSurface}" not mounted; falling back to chat`);
    }
    return <A2UIViewer ... />;
  }

  return createPortal(<A2UIViewer ... />, mount.current);
}
```

### Persistence + patches

Workspace and sidebar surfaces hold their A2UI tree in React state owned by the registry, not by the message-bubble component that triggered the spec. The registry maintains a per-surface tree + dataModel:

```typescript
interface SurfaceState {
  tree: A2UIStaticSpec | null;
  lastUpdatedAt: number;
  sourceToolCallId: string | null;
}
```

- `update_mode="replace"` (default): swap the tree entirely
- `update_mode="patch"`: shallow-merge `data` onto the existing tree's data model; component IDs that exist keep their state (so React reconciler doesn't remount a heavy WebGL widget when only its dataset changes)

The chat surface keeps the existing turn-scoped behaviour — each tool call renders fresh inside its MessageBubble; no persistence.

### Workshop W6 demo upgrade

Current W6 (A2UI Declarative UI) demos `<A2UIViewer>` rendering a form or table inline in chat. After this ships, the demo becomes:

```
User:  "Show me Munich"
Agent: chat surface → "Showing Munich"
       workspace surface → map widget renders with Munich centered (replace mode)

User:  "Now zoom in to the old town"
Agent: chat surface → "Zooming to the old town"
       workspace surface → dataModelUpdate patches the bounds property
                          The map stays mounted — no re-render flash —
                          the WebGL state preserves zoom/pan transitions

User:  "Save this view as a favourite"
Agent: modal surface → "Save view as favourite?" confirmation
       (modal requires the user click; agent cannot pop one unprompted)
```

Stronger demo than inline-only: it demonstrates the *protocol* (per-surface trees, patch semantics, persistence) rather than just a renderer.

## Implementation Plan

~3 days total. Most of it frontend; backend is a small schema addition.

### Phase 1 — Backend schema (~3h)
- [ ] Add `surface_id: str | None = None` + `update_mode: Literal["replace", "patch"] = "replace"` to the `send_a2ui_json_to_client` tool-call output schema
- [ ] Add `SkillConfig.tool_configs.a2ui.default_surface` (optional) for skill-level defaults
- [ ] Schema validation: reject `update_mode="patch"` when `surface_id is None or "chat"` (chat is turn-scoped, patches don't apply)
- [ ] Unit tests: schema round-trip, validation rejection

### Phase 2 — Frontend SurfaceRegistry + Mount + RendererSplit (~1.5d)
- [ ] `frontend/src/providers/SurfaceRegistry.tsx` — context + provider + per-surface state owner
- [ ] `frontend/src/components/protocols/A2UISurfaceMount.tsx` — registry-binding component
- [ ] Update `A2UIRenderer` to read `surfaceId` + `updateMode` from the tool-call payload and portal accordingly
- [ ] Update `MessageBubble` to pass through `surfaceId` (currently parses A2UI from tool-call results — extract `surface_id` from the payload)
- [ ] Update chat page layout to mount `workspace`, `sidebar`, `modal` surfaces
- [ ] Vitest: SurfaceRegistry register/unregister/get; portal routing; missing-mount fallback to chat with console warning
- [ ] chrome-devtools verification per `aitana-frontend-verify` skill: agent emits `surface_id=workspace` → tree lands in the workspace pane; new chat turn → workspace stays put

### Phase 3 — Patch semantics + persistence (~1d)
- [ ] dataModelUpdate handler — accept `update_mode="patch"`, shallow-merge data, preserve component identity
- [ ] Per-surface state lifecycle: turn-scoped clear on `RUN_FINISHED` for chat; session-scoped clear on session change for workspace/sidebar; modal clears on action or dismiss
- [ ] Vitest: patch preserves component identity (test by attaching a ref-counting stub component to a tree, patching data, asserting the stub was NOT remounted)
- [ ] Vitest: persistence across turns — emit two spec messages with `surface_id=workspace`, second is `patch`, assert the first's components are not unmounted

### Phase 4 — Workshop demo + docs (~0.5d)
- [ ] Update the `geo-map` workshop skill to target `surface_id=workspace`
- [ ] Add a "show me X / zoom to Y" two-turn demo to W6
- [ ] `docs/integrations/multi-surface-rendering.md` — short howto for skill authors ("declare your surface; understand persistence; use patch for live updates")
- [ ] Update [a2ui-tool-delivery.md](../../v6.1.0/implemented/a2ui-tool-delivery.md) with a forward link to this design

## Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Portal mount race — spec arrives before `A2UISurfaceMount` is registered | Medium | Registry queues specs per-surface for ~500ms; warn on fallback after timeout |
| Cross-turn state leaks user data when session changes | High | `SurfaceRegistry` subscribes to session-id changes; clears `session-scoped` surfaces on transition. Vitest pins this |
| Skills inadvertently target an unmounted surface (e.g., a fork removed `sidebar`) | Medium | Graceful fallback to chat with `console.warn` in dev + audit-log event `surface_unmounted_fallback` in prod |
| Modal surface used for adversarial prompts (agent pops a "confirm to delete everything" modal) | Medium | Modal policy requires `requiresUserGesture=true`; agent emit blocks until user explicitly invokes |
| `dataModelUpdate` patch logic mis-handles deep nested updates | Medium | v1 only supports shallow data patches; nested updates require `update_mode="replace"` until v2 |
| `@a2ui/react` library doesn't expose the hooks we need for surface routing | Low | We control the `A2UIViewer` mount; surfaces are an *outer* layer; library internals untouched |
| Workshop demo regression — existing inline demos break | Low | Default `surface_id=None` → inline-in-chat path is identical to today; backwards compat is the contract |

## Migration & Rollout

**Backwards compatible by design** — every existing skill keeps working.

1. Phase 1 ships the schema field, defaulted to `None` — no skill changes required
2. Phase 2 ships the registry + mounts — existing inline rendering unchanged
3. Phase 3 lands patch semantics — opt-in via `update_mode="patch"`
4. Phase 4 updates the workshop demo + the geo-map skill

Forks adopt by:
- Wrapping their layout with `<SurfaceRegistryProvider>`
- Adding `<A2UISurfaceMount>` for surfaces they want
- Setting `surface_id` on the relevant skills

AIPLA fork adopts immediately. a tutoring fork (client contact) can adopt for the teacher dashboard if they want — student device remains chat-primary, teacher dashboard becomes a `workspace` surface.

## Testing Strategy

- **Unit (backend):** schema validation, default propagation from `SkillConfig`
- **Unit (frontend):** `SurfaceRegistry` register/unregister/get; portal routing; missing-mount fallback; patch preserving component identity; cross-turn persistence
- **Integration (frontend):** chrome-devtools MCP verification — load a skill emitting `surface_id=workspace`, assert tree lands in the workspace pane and stays after a new chat turn
- **Workshop demo:** the two-turn "show Munich → zoom" sequence runs end-to-end against the geo-map skill
- **Adversarial:** modal surface rejects agent-initiated invocations (requires user gesture)

## Security Considerations

- **Surface policies enforce trust boundaries** — `modal.requiresUserGesture=true` prevents the agent from popping a blocking confirmation unprompted
- **Cross-session leakage** — session-scoped surfaces clear on session change; verified by test
- **Fork extension** — forks can register custom surfaces; they MUST set sensible policies (don't make `teacher-only` surfaces world-readable)
- **Audit log** — once [audit-log-and-analytics](audit-log-and-analytics.md) ships, every A2UI tool call records `surface_id` in the event metadata, so admins can see "agent emitted a workspace spec at 14:32"

## Open Questions

1. **Session reset behaviour.** When a user starts a new session, are workspace/sidebar surfaces cleared? Recommend: yes (`session-scoped` default), but `sidebar` could opt into `indefinite` if a fork wants persistent navigation context. Default policy table reflects this.
2. **Fork-level default surface override.** Should a fork be able to remap the default `chat` surface globally (e.g., AIPLA wants `workspace` as default for class-status skills)? Recommend: yes, via `SkillConfig.tool_configs.a2ui.default_surface` already in the schema; no separate fork-level override.
3. **Patch semantics depth.** v1 only supports shallow data patches. Do we need component-tree patching (add/remove/reorder children) in v1? Recommend: no — wait for a real use case; `update_mode="replace"` covers it.
4. **`@a2ui/react` hooks.** The current `<A2UIViewer>` is opaque to us; if multi-component identity preservation across patches requires library hooks, we may need to either upstream or use a different mounting strategy. Spike before Phase 3.
5. **Mobile / responsive.** On a phone, four named surfaces don't all fit. Do we collapse `workspace` + `sidebar` into a stack? Defer — first consumers (AIPLA, a tutoring fork teacher dashboard) are desktop-primary.

## Related Documents

- [A2UI tool delivery (1.0)](../../v6.1.0/implemented/a2ui-tool-delivery.md) — the protocol primitive this extends with surface routing
- [Chat message rendering (1.1)](../../v6.1.0/implemented/chat-message-rendering.md) — current inline A2UI mount point we keep backwards compat with
- [Document workspace UI (1.10)](../../v6.1.0/document-ui.md) — the split-pane layout we extend
- [Channels framework (1.6)](../../v6.1.0/channels.md) — pattern for surface routing parallels how channels route messages to adapters (the routing-by-id idea has a precedent in the codebase)
- [Audit log + analytics](audit-log-and-analytics.md) — once shipped, records `surface_id` on every A2UI event
- [Workshop tracker](../../../talks/ai-ui-protocol-stack.md) — W6 demo upgrade target
- A2UI spec: [a2ui.org](https://a2ui.org) — `surfaceId` semantics + per-surface data model
- AIPLA ADR-015 — external; surfaced this requirement
