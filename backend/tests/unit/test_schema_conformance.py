"""Generated tool declarations must conform to the Gemini proto (v6.20.0).

A downstream fork followed WORKSHOP.md verbatim and could not complete a single
chat turn: `request_confirmation`'s `dict[str, Any]` params make ADK emit
`additional_properties`, which the Gemini Express `FunctionDeclaration` proto
does not define, so it 400s the request. `enable_confirmation` defaults True,
so it was attached to every skill — first message, every demo skill, broken.

It reached a published template because Vertex tolerates the field (our dev
loop) and because **no test ever assembled the real flattened declaration
list** — the exact gap this file closes.
"""

from __future__ import annotations

import json

import pytest
from google.adk.tools import FunctionTool

from adk.elicitation import request_confirmation
from adk.schema_conformance import (
    REJECTED_SCHEMA_KEYS,
    sanitize_function_declarations,
)


def _declaration_json(func) -> str:
    decl = FunctionTool(func=func)._get_declaration()
    return decl.model_dump_json(exclude_none=True)


class TestTheDefectIsReal:
    """Pin the upstream behaviour, so we notice if ADK ever stops emitting it."""

    def test_adk_emits_a_rejected_key_for_dict_any_params(self):
        raw = _declaration_json(request_confirmation)

        assert "additional_properties" in raw, (
            "ADK no longer emits additional_properties for dict[str, Any] — "
            "the sanitizer may be retirable; verify against Gemini Express first."
        )

    def test_the_offending_params_are_the_reported_ones(self):
        decl = FunctionTool(func=request_confirmation)._get_declaration()
        schema = json.loads(decl.model_dump_json(exclude_none=True))
        offenders = {
            name
            for name, spec in schema["parameters"]["properties"].items()
            if "additional_properties" in json.dumps(spec)
        }

        assert offenders == {"fields", "context"}


class _FakeSchema:
    """Minimal stand-in with the nesting shape ADK actually produces."""

    def __init__(self, payload: dict):
        self.__dict__.update(payload)


class _FakeDeclaration:
    def __init__(self, parameters):
        self.parameters = parameters


class _FakeTool:
    def __init__(self, declarations):
        self.function_declarations = declarations


class _FakeConfig:
    def __init__(self, tools):
        self.tools = tools


class _FakeRequest:
    def __init__(self, tools):
        self.config = _FakeConfig(tools)


def _request_with(payload: dict) -> _FakeRequest:
    return _FakeRequest([_FakeTool([_FakeDeclaration(_FakeSchema(payload))])])


class TestSanitizer:
    def test_strips_the_key_nested_inside_any_of_and_items(self):
        """The real shape: any_of -> items -> additional_properties."""
        req = _request_with(
            {
                "properties": {
                    "fields": {
                        "any_of": [
                            {"items": {"additional_properties": True, "type": "OBJECT"}},
                            {"type": "NULL"},
                        ]
                    }
                }
            }
        )

        removed = sanitize_function_declarations(req)

        assert removed == 1
        assert "additional_properties" not in json.dumps(
            req.config.tools[0].function_declarations[0].parameters.__dict__
        )

    def test_strips_the_camelCase_spelling_too(self):
        req = _request_with({"properties": {"c": {"additionalProperties": True}}})

        assert sanitize_function_declarations(req) == 1

    def test_strips_every_occurrence_not_just_the_first(self):
        req = _request_with(
            {
                "properties": {
                    "a": {"additional_properties": True},
                    "b": {"items": {"additional_properties": True}},
                }
            }
        )

        assert sanitize_function_declarations(req) == 2

    def test_leaves_legitimate_schema_untouched(self):
        """Over-stripping would break tool calling — worse than the bug."""
        req = _request_with({"properties": {"message": {"type": "STRING"}, "kind": {"type": "STRING"}}})

        assert sanitize_function_declarations(req) == 0
        params = req.config.tools[0].function_declarations[0].parameters.__dict__
        assert params["properties"]["message"]["type"] == "STRING"

    @pytest.mark.parametrize(
        "req",
        [
            _FakeRequest([]),
            _FakeRequest([_FakeTool([])]),
            _FakeRequest([_FakeTool([_FakeDeclaration(None)])]),
        ],
    )
    def test_tolerates_empty_and_partial_shapes(self, req):
        assert sanitize_function_declarations(req) == 0

    def test_never_raises_on_a_malformed_request(self):
        """A sanitizer that can crash the call is worse than the 400 it prevents."""
        assert sanitize_function_declarations(object()) == 0


def test_the_model_seam_calls_the_sanitizer():
    """Guard the WIRING, not just the function.

    A correct sanitizer that nothing calls is the failure mode this catches —
    and it is exactly how the original bug survived: the pieces existed, the
    assembled result was never checked.
    """
    import inspect

    from adk import resilient_llm

    source = inspect.getsource(resilient_llm.ResilientLlm.generate_content_async)
    assert "sanitize_function_declarations(llm_request)" in source


def test_rejected_key_set_is_not_empty():
    assert REJECTED_SCHEMA_KEYS


class TestAgainstTheRealDeclaration:
    """The tests that actually matter.

    The dict-based fixtures above all passed against a walker that could only
    handle dicts — while the REAL schema (a tree of pydantic models) kept its
    `additional_properties` nested under `any_of -> items`. A sanitizer that
    reports success and changes nothing is worse than none, because it also
    removes the incentive to look again.

    So: assert against a genuine `_get_declaration()`, end to end.
    """

    def _real_request(self):
        decl = FunctionTool(func=request_confirmation)._get_declaration()
        tool = type("T", (), {"function_declarations": [decl]})()
        config = type("C", (), {"tools": [tool]})()
        return type("R", (), {"config": config})(), decl

    def test_real_declaration_is_clean_after_sanitizing(self):
        req, decl = self._real_request()

        sanitize_function_declarations(req)

        assert "additional_properties" not in decl.model_dump_json()

    def test_real_declaration_keeps_its_parameters(self):
        """Over-stripping the real schema would break tool calling outright."""
        req, decl = self._real_request()

        sanitize_function_declarations(req)
        schema = json.loads(decl.model_dump_json(exclude_none=True))

        assert set(schema["parameters"]["properties"]) >= {"message", "kind", "fields", "context"}
        assert schema["parameters"]["properties"]["message"]["type"] == "STRING"

    def test_sanitizing_is_idempotent(self):
        req, _decl = self._real_request()

        first = sanitize_function_declarations(req)
        second = sanitize_function_declarations(req)

        assert first > 0
        assert second == 0
