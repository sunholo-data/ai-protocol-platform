# Browser-only A2UI review details

Direct actions keep authorization in a safe, server-stashed control surface.
An authenticated session owner clicks `direct:<name>` there; the existing
skill opt-in, stash/kind registration, declared-tool and user-tool grants all
run before the handler. The handler must resolve and validate the canonical
request/version from that envelope rather than trusting browser details.

Return confidential display content separately:

```python
ActionResult(
    status="ok",
    message="Review opened.",  # nonconfidential status
    private_surfaces=[{
        "surface_id": f"private:{ctx.surface_id}:review",
        "messages": detail_a2ui_messages,
        "artifact": {"kind": "review", "title": "Private review"},
    }],
)
```

Every message in a private descriptor must target its `surface_id`, which
must start with `private:<control surface id>:`. Ordinary `messages` may patch
only the safe control surface. Private descriptors return directly to the
browser with `Cache-Control: no-store`; they never enter the backend stash,
ADK events or action records. A response with private descriptors suppresses
`tell_ai` recording even if the action registration uses its default.
`status`, `message`, ordinary patches and artifact labels should be safe.

The generic mount calls
`registry.appendPrivateMessages(surfaceId, messages, sourceId, artifact)`.
This creates a normal A2UI-rendered workbench artifact with immutable
`modelExport: false` and session-scoped lifetime. New private responses replace
previous details. Mount policy overrides cannot relax the privacy boundary.
Private surfaces are display-only: decision buttons remain on the control
surface. Server schemas reject actions on the reserved `private:` namespace.

`readA2uiSurfaceState()` excludes private surfaces from both ordinary chat and
action-triggered model turns. The backend repeats namespace filtering before
building ADK inputs and model-facing surface-context summaries. Session changes
dispose details; late direct-action and image responses cannot resurrect them.
Details are intentionally absent on reload; the owner opens a fresh review.

Verification covers owner/tool refusals, private patch target isolation, no
state/event persistence, real A2UI SDK registration plus an actual ordinary
`useSkillAgent.sendMessage()` call with canaries absent from outbound props,
display-only actions, session disposal and stale-response rejection. These
checks establish the protocol boundary; real-device UX acceptance is separate.

## Deterministic entry before the first chat turn

Skills can declare fixed entry buttons in `toolConfigs.a2ui.direct_launchers`:

```yaml
direct_launchers:
  - name: owner_board
    label: Waiting for you
    tool: list_owner_board
```

Register a matching `register_launcher(name, requires_tool, handler)` from
`adk.a2ui_direct_launchers`; its async handler receives `LauncherContext`
(user, session_id, skill_id) and returns `LaunchResult(surface_id, messages,
artifact, message="Opened.")`. Return safe control data only.

The generic Workspace Home buttons POST only `{name}` to
`/api/skills/{skill}/sessions/{session}/surface-launch-direct`. The server checks
skill visibility, explicit surface-write opt-in, matching declaration and
registered required tool, skill tools and caller grants. It checks existing
session ownership before bootstrap and rechecks owner and canonical skill
afterwards. Fresh sessions use the existing bootstrap routine; no model call is
needed. Successful results are stashed as normal A2UI control surfaces and mounted
through SurfaceRegistry. Disabled services and refusals remain visible errors.
Browser-supplied tool names, arguments and refs are rejected by the request schema.
