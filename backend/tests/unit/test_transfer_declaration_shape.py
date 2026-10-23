"""ADK-contract guard: the `transfer_to_agent` FunctionDeclaration the model sees.

Our handoff (v6.10.0 unified-adk-handoff, contract C6) relies on ADK's native
``TransferToAgentTool``: every accessible delegate is wired as a sub_agent under
a slug name (``_delegate_agent_name``), ADK enum-constrains ``agent_name`` to
those names, and ``make_handoff_policy_callback`` keys its floor lookup on them.
The declaration is therefore part of our contract: if its shape moves, the model
sees a different tool and the policy callback may see different args.

ADK 2.x changes this seam (changelog 609: an opt-in ``transfer_reason`` argument
behind ``include_transfer_reason``; 652/487: transfers restricted to declared
targets). Upgrade sub-doc 03 keeps ``include_transfer_reason=False`` so the
arguments stay unchanged. ADK 2 emits JSON Schema; the target enum and
required agent_name still match the policy, which this test proves.

The stub model drives the REAL Runner and captures the ``LlmRequest`` ADK built,
so the snapshot is exactly what a real model would receive.

Part of `make adk-conformance`. See docs/design/template/template-adk-2-regression-gate.md.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.agent import _delegate_agent_name, make_handoff_policy_callback
from adk.mcp_observability import compose_before_tool_callbacks, make_mcp_before_tool_callback
from db.models import DelegateRule

pytestmark = pytest.mark.adk_contract

# Captured LlmRequests — module-level, not a pydantic field (see the template).
_REQUESTS: list = []


class _CapturesRequest(BaseLlm):
    """A stub front-door that records what ADK sent it and answers in text."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _REQUESTS.append(llm_request)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))


class _NeverCalled(BaseLlm):
    async def generate_content_async(self, llm_request, stream: bool = False):
        raise AssertionError("no delegate may run — the door answers in text")
        yield  # unreachable; makes this an async generator


def _skill(skill_id: str, slug: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(skill_id=skill_id, slug=slug, display_name=name, name=name, description=f"{name}.")


def _transfer_declaration() -> tuple[types.FunctionDeclaration, set[str]]:
    """Build a door the way ``create_agent`` does (slug-named sub_agents, one
    auto + one confirm-floor stub, the composed before_tool chain), run one turn
    and return the ``transfer_to_agent`` declaration from the captured request,
    plus the names the floor policy keys on."""
    _REQUESTS.clear()
    taken: set[str] = set()
    auto_skill = _skill("a1b2c3d4-auto", "demo-ppa-expert", "PPA Expert")
    confirm_skill = _skill("26124699-f558", "demo-obligation-analysis", "PPA Obligation Analysis")
    auto_name = _delegate_agent_name(auto_skill, taken)
    confirm_name = _delegate_agent_name(confirm_skill, taken)
    delegate_map = {
        auto_name: (auto_skill, DelegateRule(skill=auto_skill.skill_id, floor="auto")),
        confirm_name: (confirm_skill, DelegateRule(skill=confirm_skill.skill_id, floor="confirm")),
    }
    door = LlmAgent(
        name="door",
        model=_CapturesRequest(model="stub-door"),
        sub_agents=[
            LlmAgent(name=auto_name, model=_NeverCalled(model="stub-auto"), description=auto_skill.description),
            LlmAgent(
                name=confirm_name, model=_NeverCalled(model="stub-confirm"), description=confirm_skill.description
            ),
        ],
        before_tool_callback=compose_before_tool_callbacks(
            make_handoff_policy_callback("door", delegate_map),
            make_mcp_before_tool_callback(),
        ),
    )

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="u", app_name="test")
    runner = Runner(agent=door, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="who can help?")])
    list(runner.run(new_message=message, user_id="u", session_id=session.id))

    assert len(_REQUESTS) == 1, f"expected one model call, got {len(_REQUESTS)}"
    declarations = [
        fd for tool in (_REQUESTS[0].config.tools or []) for fd in (getattr(tool, "function_declarations", None) or [])
    ]
    matches = [fd for fd in declarations if fd.name == "transfer_to_agent"]
    assert len(matches) == 1, (
        f"expected exactly one transfer_to_agent declaration, got {[fd.name for fd in declarations]}"
    )
    return matches[0], set(delegate_map)


def _shape(fd: types.FunctionDeclaration) -> dict:
    """The parts of the declaration our handoff depends on, normalised so the
    snapshot reads the same whether ADK emits ``parameters`` (a Schema) or
    ``parameters_json_schema`` (a dict)."""
    if fd.parameters is not None:
        props = fd.parameters.properties or {}
        return {
            "schema_form": "parameters",
            "type": str(fd.parameters.type.value if fd.parameters.type else None),
            "properties": {
                name: {
                    "type": str(s.type.value if s.type else None),
                    "enum": list(s.enum) if s.enum is not None else None,
                }
                for name, s in props.items()
            },
            "required": sorted(fd.parameters.required or []),
        }
    js = fd.parameters_json_schema or {}
    return {
        "schema_form": "parameters_json_schema",
        "type": str(js.get("type", "")).upper(),
        "properties": {
            name: {"type": str(s.get("type", "")).upper(), "enum": s.get("enum")}
            for name, s in (js.get("properties") or {}).items()
        },
        "required": sorted(js.get("required") or []),
    }


# The snapshot. A change here is a contract change: update it only together with
# the handoff policy and the 01/03 upgrade docs, never to make a bump go green.
_EXPECTED_SHAPE = {
    "schema_form": "parameters_json_schema",
    "type": "OBJECT",
    "properties": {
        "agent_name": {
            "type": "STRING",
            # Sub_agent order, under the slug names _delegate_agent_name produces.
            "enum": ["demo_ppa_expert", "demo_obligation_analysis"],
        },
    },
    "required": ["agent_name"],
}


def test_transfer_declaration_shape():
    fd, _ = _transfer_declaration()
    assert _shape(fd) == _EXPECTED_SHAPE, (
        f"transfer_to_agent declaration changed: {_shape(fd)!r} — the handoff policy keys on "
        "`agent_name` only; a new argument (e.g. 2.x `transfer_reason`) or a moved enum is a "
        "contract change (keep include_transfer_reason=False until it is a measured change)"
    )


def test_transfer_declaration_enum_matches_the_policy_keys():
    """Every name the model may choose is a key the floor policy recognises — an
    enum value without a policy entry would bypass the confirm floor."""
    fd, policy_keys = _transfer_declaration()
    enum = _shape(fd)["properties"]["agent_name"]["enum"] or []
    assert set(enum) == policy_keys, f"enum {enum!r} != handoff-policy keys {sorted(policy_keys)!r}"
