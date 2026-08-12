"""A2UI toolset factory — creates a surface-aware SendA2uiToClientToolset.

Background
----------

Replaces the A2UI_INSTRUCTION_SUFFIX fenced-block convention. A2UI JSON now
travels via TOOL_CALL_* AG-UI events rather than embedded in message text.
Workshop W6c — A2UI delivery: the tool-call path is the published solution.

MULTI-SURFACE-A2UI M1 — surface routing
---------------------------------------

A2UI ([a2ui.org](https://a2ui.org)) treats a *surface* as a first-class
concept (chat / workspace / sidebar / modal). Skills can declare a default
surface in `SkillMetadata.tool_configs.a2ui.default_surface`; the agent
factory passes that to `make_a2ui_toolset()` which threads it through to
every tool call.

Spike outcome — wrapper approach (option (c) from the sprint plan)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `a2ui-agent-sdk`'s `_SendA2uiJsonToClientTool` accepts a single string
arg `a2ui_json` (declared in `_get_declaration()`); the FunctionDeclaration
schema is fixed. Validation runs against the A2UI schema, which only
recognises four message types (`createSurface`, `updateComponents`,
`updateDataModel`, `deleteSurface` for v0.9) — adding `surface_id` as a
top-level message key would fail validation ("Unknown message type").

The `_meta` / metadata extension point on `create_a2ui_part` (used by
`A2uiPartConverter`) is one option, but our backend never calls the
converter — the AG-UI bridge ships the raw tool-result dict (the one
returned from `run_async`) straight to the frontend as a TOOL_CALL_RESULT
event. The frontend's `MessageBubble.parseA2UIResult()` reads
`parsed.validated_a2ui_json`.

So the cleanest, fully-backwards-compatible extension is to **augment the
tool result dict with optional sibling keys** alongside
`validated_a2ui_json`:

    # Pre-M1 (unchanged when default_surface is None):
    {"validated_a2ui_json": [...]}

    # Post-M1 when the skill declares a surface:
    {"validated_a2ui_json": [...], "surface_id": "workspace",
     "update_mode": "replace"}

`SurfaceAwareA2uiToolset` subclasses the SDK toolset and substitutes a
`_SurfaceAwareTool` for `_SendA2uiJsonToClientTool`. The inner tool calls
the SDK's `run_async` (which does all the validation we want to keep) then
overlays the surface keys on success. Error envelopes are passed through
untouched — surface keys never leak onto failure results.

This decision is documented here so future maintainers see the reasoning
without having to redo the spike.
"""

from __future__ import annotations

import warnings
from typing import Any, Literal

from a2ui.adk.send_a2ui_to_client_toolset import SendA2uiToClientToolset
from a2ui.basic_catalog import BasicCatalog
from a2ui.schema.catalog import A2uiCatalog
from a2ui.schema.manager import A2uiSchemaManager
from pydantic import BaseModel, Field, model_validator

UpdateMode = Literal["replace", "patch"]


_CATALOG: A2uiCatalog | None = None


def _load_catalog() -> A2uiCatalog:
    config = BasicCatalog.get_config("0.9")
    manager = A2uiSchemaManager(version="0.9", catalogs=[config])
    return manager._supported_catalogs[0]


def _get_catalog() -> A2uiCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _load_catalog()
    return _CATALOG


# Known-good v0.9 wire-format example, appended to the system prompt by the
# SDK after the auto-injected A2UI JSON SCHEMA block. The Gemini-2.5 family
# has been observed to confabulate the v0.8 nested shape (`{component: {…}}`)
# or the not-a-message shape (`{root, components, data}`) when the prompt
# only contains the schema. Showing one concrete, valid array of messages
# anchors it on the right wire format.
#
# Don't strip surfaceId — the validator does not default it.
_A2UI_V09_EXAMPLE = """
### Example call to send_a2ui_json_to_client

The argument `a2ui_json` is a JSON string. The decoded value is an **array of
messages**, each of which is one of `createSurface`, `updateComponents`,
`updateDataModel`, `deleteSurface`. Components are **flattened** — the
component type is a string at `component`, not a nested object.

A complete dashboard render looks like this:

```json
[
  {
    "version": "v0.9",
    "createSurface": {
      "surfaceId": "workspace",
      "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json"
    }
  },
  {
    "version": "v0.9",
    "updateComponents": {
      "surfaceId": "workspace",
      "components": [
        {"id": "root", "component": "Column", "children": ["title", "users", "divider", "footnote"]},
        {"id": "title", "component": "Text", "text": "Workspace Surface Demo", "variant": "h2"},
        {"id": "users", "component": "Text", "text": {"path": "/activeUsers"}, "variant": "h3"},
        {"id": "divider", "component": "Divider"},
        {"id": "footnote", "component": "Text", "text": {"path": "/footnote"}, "variant": "caption"}
      ]
    }
  },
  {
    "version": "v0.9",
    "updateDataModel": {
      "surfaceId": "workspace",
      "value": {
        "activeUsers": "42 users online",
        "footnote": "Workspace persists across chat turns."
      }
    }
  }
]
```

Rules the validator enforces:
- Top-level value is an array of message objects.
- Every message has `version: "v0.9"` AND exactly one of the four message keys.
- The component tree root MUST have `id: "root"` (the validator hardcodes this).
- Component refs (`children`, `child`, `contentChild`) are component-id strings, not inline objects.
- Dynamic values come from the data model via `{"path": "/key"}`; literals are bare strings/numbers.
""".strip()


class A2uiToolConfig(BaseModel):
    """Skill-level A2UI defaults read from `tool_configs.a2ui` in SkillMetadata.

    Lives next to the toolset factory rather than `db/models/__init__.py`
    because `tool_configs` is a loosely-typed `dict[str, dict]` (the
    Agent Skills spec allows arbitrary per-tool config). Parsing into a
    typed model is opt-in via `A2uiToolConfig.from_tool_configs(...)`.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Whether to attach the A2UI toolset to this skill. Default True "
            "preserves backwards-compat for all existing workshop demos. "
            "Set False for chat-only skills that should never call "
            "send_a2ui_json_to_client — the model literally can't see the "
            "tool, saving ~200 tokens/turn of schema injection."
        ),
    )
    default_surface: str | None = Field(
        default=None,
        description=(
            "Surface id where this skill's A2UI specs render by default. "
            'Built-in: "chat" | "workspace" | "sidebar" | "modal". '
            'Forks may declare custom ids (e.g. "aipla:teacher-grid"). '
            "None = inline in the chat bubble (pre-M1 behaviour)."
        ),
    )
    default_update_mode: UpdateMode = Field(
        default="replace",
        description=(
            "How the surface should incorporate this spec. "
            '"replace" (default) swaps the surface tree entirely. '
            '"patch" merges `data` onto the existing tree, preserving '
            "component identity. `patch` requires a persistent surface "
            '(NOT None, NOT "chat") because the chat surface is turn-scoped.'
        ),
    )
    allow_surface_context_writes: bool = Field(
        default=False,
        description=(
            "Sprint 2.10 — opt-in flag for the surface → agent context "
            "loop. Default false (deny). When true: the frontend "
            "snapshots SurfaceModel.dataModel on every outbound turn and "
            "rides it back on `forwardedProps.a2ui_surface_state`; user "
            "actions on the surface POST to "
            "`/api/sessions/{id}/surface-action` and persist under the "
            "`a2ui_surface_context.{surfaceId}.lastAction` session-state "
            "namespace. The InstructionProvider injects both into the "
            "next agent prompt. See "
            "docs/design/v6.2.0/implemented/a2ui-surface-context.md."
        ),
    )
    allow_action_triggered_runs: bool = Field(
        default=False,
        description=(
            "If true, this skill accepts POST .../surface-action-run requests "
            "that bundle the A2UI action write with an agent run. Distinct trust "
            "grant from allow_surface_context_writes — opt in here only if the "
            "skill is designed to be driven entirely by surface clicks. See "
            "docs/design/v6.1.0/action-triggered-agent-turn.md."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _reject_patch_against_turn_scoped(self) -> A2uiToolConfig:
        if not self.enabled:
            return self  # surface/mode rules irrelevant when A2UI is disabled
        if self.default_update_mode == "patch" and (self.default_surface is None or self.default_surface == "chat"):
            raise ValueError(
                'default_update_mode="patch" requires a persistent surface '
                "(workspace, sidebar, modal, or a fork-defined custom id). "
                'The chat surface is turn-scoped; "patch" cannot apply.'
            )
        return self

    @classmethod
    def from_tool_configs(cls, tool_configs: dict[str, Any] | None) -> A2uiToolConfig:
        """Build the typed config from a raw `tool_configs` dict.

        Tolerant: missing `a2ui` key OR a non-dict value falls back to the
        default (no surface). Any other shape (a dict with unknown keys
        or invalid combinations) raises `pydantic.ValidationError` so
        misconfiguration is loud, not silent.
        """
        if not tool_configs:
            return cls()
        raw = tool_configs.get("a2ui")
        if raw is None or not isinstance(raw, dict):
            return cls()
        return cls.model_validate(raw)


class _SurfaceAwareTool(SendA2uiToClientToolset._SendA2uiJsonToClientTool):
    """Inner tool that overlays `surface_id`/`update_mode` on the result.

    Subclasses the SDK's private inner tool. This keeps:
      - the LLM-facing tool name (`send_a2ui_json_to_client`) identical
      - the LLM-facing arg schema (`a2ui_json: string`) identical
      - the schema validation pipeline (parse_and_fix + catalog validator) intact

    What we add: on a successful run (result contains
    `validated_a2ui_json`), augment the result dict with the surface
    siblings — but ONLY when the wrapper was configured with a surface.
    On the error path, pass the SDK envelope through unchanged so we
    never imply a surface for a failed render.
    """

    def __init__(
        self,
        a2ui_catalog: A2uiCatalog,
        a2ui_examples: str,
        default_surface: str | None,
        default_update_mode: UpdateMode,
    ) -> None:
        super().__init__(a2ui_catalog, a2ui_examples)
        self._default_surface = default_surface
        self._default_update_mode = default_update_mode

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        result = await super().run_async(args=args, tool_context=tool_context)
        if not isinstance(result, dict):
            return result
        if self._default_surface is None:
            return result
        # Only augment successful results — never leak surface onto an error envelope.
        if self.VALIDATED_A2UI_JSON_KEY not in result:
            return result
        return {
            **result,
            "surface_id": self._default_surface,
            "update_mode": self._default_update_mode,
        }


class SurfaceAwareA2uiToolset(SendA2uiToClientToolset):
    """SendA2uiToClientToolset that injects surface routing into tool results.

    `default_surface=None` makes this byte-identical to the upstream
    toolset (the inner tool short-circuits and returns the SDK result
    unchanged). When a surface is set, every successful tool call's
    result dict gains `surface_id` + `update_mode` siblings.
    """

    def __init__(
        self,
        a2ui_enabled: bool,
        a2ui_catalog: A2uiCatalog,
        a2ui_examples: str,
        default_surface: str | None = None,
        default_update_mode: UpdateMode = "replace",
    ) -> None:
        # Validate up-front via the same Pydantic rule so misconfigured
        # skills fail at agent-build time, not at the first tool call.
        A2uiToolConfig(default_surface=default_surface, default_update_mode=default_update_mode)

        # Initialise the BaseToolset parent state without invoking the
        # upstream SDK constructor (which would build a vanilla inner
        # tool we'd have to discard).
        from google.adk.tools import base_toolset

        base_toolset.BaseToolset.__init__(self)
        self._a2ui_enabled = a2ui_enabled
        self._ui_tools = [
            _SurfaceAwareTool(
                a2ui_catalog=a2ui_catalog,
                a2ui_examples=a2ui_examples,
                default_surface=default_surface,
                default_update_mode=default_update_mode,
            )
        ]
        self.default_surface = default_surface
        self.default_update_mode: UpdateMode = default_update_mode


def make_a2ui_toolset(
    *,
    default_surface: str | None = None,
    default_update_mode: UpdateMode = "replace",
    config: A2uiToolConfig | None = None,
) -> SurfaceAwareA2uiToolset:
    """Return a surface-aware A2UI toolset.

    Args:
        default_surface: Surface id this skill's A2UI specs render into by
            default. `None` (the default) keeps pre-M1 behaviour: inline in
            the chat bubble. Pass `"workspace"` / `"sidebar"` / `"modal"`
            for the built-in surfaces, or a fork-defined custom id.
        default_update_mode: `"replace"` (default) or `"patch"`. Patch mode
            requires a persistent surface — passing `"patch"` with
            `default_surface in (None, "chat")` raises `ValueError`.
        config: Alternative entry point — pass an already-validated
            `A2uiToolConfig` instance. Mutually exclusive with the kwargs
            above (the kwargs are ignored when `config` is provided).

    Returns:
        A `SurfaceAwareA2uiToolset` (which IS-A `SendA2uiToClientToolset`).
        When `default_surface is None`, the toolset's tool results are
        byte-identical to the pre-M1 SDK output.

    Raises:
        ValueError: if the surface/update-mode combination is illegal.

    Experimental — requires google-adk>=1.28.0 and a2ui-agent-sdk>=0.2.1.
    Suppresses the expected experimental warning at call site.
    """
    if config is not None:
        default_surface = config.default_surface
        default_update_mode = config.default_update_mode
    else:
        # Validate the loose-kwarg path through the same Pydantic rule so
        # error messages are identical across both entry points.
        try:
            A2uiToolConfig(
                default_surface=default_surface,
                default_update_mode=default_update_mode,
            )
        except Exception as exc:  # pragma: no cover - re-raised below
            # Surface as ValueError so the agent factory's callers can
            # match on a single exception type without importing pydantic.
            raise ValueError(str(exc)) from exc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return SurfaceAwareA2uiToolset(
            a2ui_enabled=True,
            a2ui_catalog=_get_catalog(),
            a2ui_examples=_A2UI_V09_EXAMPLE,
            default_surface=default_surface,
            default_update_mode=default_update_mode,
        )
