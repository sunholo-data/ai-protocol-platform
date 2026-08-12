# Multi-Surface A2UI — Skill Author Howto

**Audience:** anyone authoring a skill that returns A2UI components and wants
them to render somewhere other than the chat bubble (a dashboard, a sidebar,
a confirmation modal).

**Goal:** ship an A2UI surface target in under 30 minutes by declaring the
right field in your `SkillConfig` and (optionally) setting `update_mode` for
live updates.

**Deeper reference:** the [design doc](../design/v6.2.0/implemented/multi-surface-rendering.md)
covers the rationale, the layout, and the protocol. This howto is the
operating manual for skill authors.

---

## TL;DR

```yaml
# In your skill's SKILL.md or YAML, declare a default surface.
# Anything the skill emits via the A2UI toolset lands there automatically.
toolConfigs:
  a2ui:
    default_surface: workspace      # chat | workspace | sidebar | modal | <custom>
    default_update_mode: replace    # replace (default) | patch
```

If you set nothing, A2UI still works exactly as it always did — your
components render inline in the chat bubble (the `chat` surface). The
multi-surface system is **opt-in by default**.

---

## 1. Pick a surface

Four surfaces ship in the template. Forks can declare more.

| Surface | When to use | Lifecycle |
|---------|-------------|-----------|
| `chat` | Conversational signal — acknowledgements, transient text, simple replies | Turn-scoped (each call renders in the bubble that produced it; new turns push old content up) |
| `workspace` | Primary work area — live dashboards, search-result tables, open artefacts, maps | Session-scoped (persists across chat turns; clears on session change) |
| `sidebar` | Persistent context — current class, current group, navigation aids | Session-scoped (same as workspace) |
| `modal` | Focused blocking tasks — confirmations, approvals, auth flows | Turn-scoped + must be user-initiated (the agent cannot pop one unprompted) |

**Rule of thumb:** if the user's eyes need to stay on the content while the
conversation continues underneath, you want `workspace`. If the answer is "I
acknowledge — here's the result" and the conversation moves on, you want
`chat`.

---

## 2. Declare it in your skill config

The cleanest path is a per-skill default. Add to your `SkillConfig.tool_configs.a2ui`:

```yaml
toolConfigs:
  a2ui:
    default_surface: workspace
    default_update_mode: replace
```

Every `send_a2ui_json_to_client` tool call this skill emits now targets the
workspace surface. No agent-prompt changes required.

To override per-call (e.g., the agent decides at runtime whether to surface
to workspace or chat), pass the keys explicitly — see §6 below.

---

## 3. Replace mode (default)

The simplest path. Every emit replaces the surface's current tree. Good for
"here's the new dashboard view" patterns.

```yaml
default_surface: workspace
default_update_mode: replace
```

When your skill emits A2UI:

```python
# Inside your skill's agent prompt or programmatic flow — the toolset
# wraps the actual emit. You just construct the spec as normal.
{
    "root": "dashboard",
    "components": [
        {"id": "dashboard", "component": {"kind": "grid", "rows": ...}},
    ],
    "data": {"week": "2026-W21"},
}
```

The workspace renders this. Next turn the agent emits a fresh spec — the
old one is replaced atomically.

---

## 4. Patch mode (live updates)

For surfaces where you want to update the data without re-rendering the
whole tree — say a map widget where you change the bounds without remounting
the WebGL canvas — use `update_mode: patch`.

```yaml
default_surface: workspace
default_update_mode: replace      # first call seeds the surface
# Then override per-call to patch:
```

The agent emits an initial `replace` spec, then subsequent `patch` calls
shallow-merge their `data` into the existing tree:

```python
# Turn 1: replace
{
    "root": "map",
    "components": [{"id": "map", "component": {"kind": "map"}}],
    "data": {"bounds": "Munich", "zoom": 11},
}

# Turn 2: patch (only data flows; components reused by reference)
{
    "root": "map",
    "components": [{"id": "map", "component": {"kind": "map"}}],
    "data": {"zoom": 15},   # zooms without remounting the map
}
```

**Why this matters:** components stay mounted across patches. A map widget
keeps its WebGL state, animation transitions are smooth, scroll position
stays put. The reconciler sees the same `components` array reference and
does NOT remount.

**Constraints:**
- Only `workspace` and `sidebar` accept patches by default. `chat` and
  `modal` are turn-scoped and patches are ignored (with a dev warning).
- Patch with no prior tree is treated as `replace` with a data-only spec
  (recoverable; a dev warning fires). Always emit `replace` first.

---

## 5. The modal surface — user-gesture only

Modal is the one surface the agent cannot pop unprompted. The policy:
`requiresUserGesture: true`.

In practice this means a modal A2UI spec only renders when the inbound
event carries a `data-user-initiated` flag. This protects against
adversarial prompts ("agent: pop a 'confirm to delete everything' modal").

For v1, modal-pop happens via:

1. The user explicitly invokes a slash command (e.g., `/approve`) — the
   command parser sets the gesture flag, and the agent's emit goes through.
2. The user clicks a button rendered in another surface (chat or workspace)
   that triggers an `onAction` flow — the action handler can dispatch a
   modal-targeted emit with the flag set.

If your skill emits modal A2UI from an agent-driven flow without a gesture,
the framework rejects it. Use `chat` or `workspace` instead, or design a
two-step flow where the user confirms first.

---

## 6. Per-call override (advanced)

When `default_surface` isn't enough — e.g., the agent decides at runtime
whether to surface to workspace or just acknowledge in chat — you can
override per emit. The mechanics are agent-side: the agent calls
`send_a2ui_json_to_client` with the standard payload, and the wrapper
toolset overlays the surface fields onto the result based on the skill's
config + any per-call hints.

In v1 the override is via the skill's *system prompt*. Future versions
may expose this as an explicit tool parameter.

```text
You can target an A2UI tool result to a named surface by setting
`surface_id` in the validated spec. Use "workspace" for the dashboard,
"chat" for inline acknowledgements. Default is workspace.
```

(This is illustrative — the exact prompt depends on your skill's
conventions.)

---

## 7. Custom surfaces (forks)

A fork can declare new surfaces alongside the four defaults. Two steps:

### Step 1 — declare the mount in your layout

```typescript
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";

function MyForkLayout() {
  return (
    <SurfaceRegistryProvider>
      <main>
        {/* Existing chat layout */}
        <ChatColumn />

        {/* Custom surface mount for a teacher dashboard */}
        <A2UISurfaceMount
          surfaceId="teacher-grid"
          policy={{ persistence: "session-scoped", acceptsPatches: true, requiresUserGesture: false }}
          className="w-full"
        />
      </main>
    </SurfaceRegistryProvider>
  );
}
```

### Step 2 — point a skill at it

```yaml
toolConfigs:
  a2ui:
    default_surface: teacher-grid
    default_update_mode: patch
```

**Naming convention for forks:** prefix custom surface IDs with your fork's
name to avoid collisions with built-in defaults — `aipla:teacher-grid`,
`playground:group-1`. The framework doesn't enforce this, but it'll save
you a debugging session when a future template adds a `dashboard` default
that conflicts with yours.

---

## 8. Common pitfalls

### Pre-push CI parity

Tests cover surface routing and policy enforcement. Run **both** lint and
tests before pushing:

```bash
cd backend && make lint && make test-fast
cd frontend && npm run quality:check     # includes lint + tsc + tests + build
```

The `quality:check:fast` and `make lint`-only variants skip tests and have
historically masked CI failures (see `backend/CLAUDE.md` §Pre-push checklist).

### Backwards compatibility

Don't worry about breaking existing skills. The framework is opt-in by
default — skills without `tool_configs.a2ui.default_surface` continue to
render inline in the chat bubble exactly as they did before M3. A regression
test pins this contract.

### Modal abuse

If you find yourself wanting the agent to pop a modal unprompted, you
probably want `workspace` instead. Modal is reserved for genuine blocking
actions the user must consent to — destructive operations, auth flows,
out-of-band confirmations.

### Session leakage

`workspace` and `sidebar` clear on session change by default. If your
fork relies on a sidebar surface persisting across sessions (e.g., a
permanent navigation tree), override the policy to `persistence: "indefinite"`.
Be aware that you're then responsible for clearing it when appropriate.

### Patch identity-preservation

For the WebGL/canvas/heavy-component case to actually work, the
`components` array in the patch payload **must** be the same shape as
the prior tree (same IDs in the same order). Use `update_mode: patch`
ONLY for data updates — adding or removing components requires
`update_mode: replace`.

---

## 9. End-to-end demo

The shipped `document-analyst` template skill is wired with
`default_surface: workspace` — clone it, install, and you'll see
document summary outputs render in the workspace pane rather than
inline in chat. Try:

1. Upload a document to the skill
2. Ask "summarise this and show the structure"
3. The chat says "Here's the summary"
4. The workspace pane renders the structured outline (table of contents,
   key topics, chart of section lengths)
5. Continue the conversation — the workspace stays put while chat scrolls

To turn it off temporarily, comment out the `a2ui:` block in the skill's
`SKILL.md` and the renderer falls back to inline-in-chat.

---

## Related

- [Multi-surface design doc](../design/v6.2.0/implemented/multi-surface-rendering.md) — full design, axiom alignment, architecture
- [A2UI tool delivery](../design/v6.1.0/implemented/a2ui-tool-delivery.md) — the protocol primitive surfaces extend
- [Channels adapter howto](channels-adapter-howto.md) — sibling pattern; channel routing parallels surface routing
- A2UI spec: [a2ui.org](https://a2ui.org) — protocol-level `surfaceId` semantics
