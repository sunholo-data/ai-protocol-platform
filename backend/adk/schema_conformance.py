"""Strip schema fields the Gemini API's FunctionDeclaration proto rejects.

Why this exists (v6.20.0, downstream fork entry #2). A tool parameter typed
``dict[str, Any]`` makes ADK render a schema carrying ``additional_properties``:

    {"any_of": [{"items": {"additional_properties": true, "type": "OBJECT"},
                 "type": "ARRAY"}, {"type": "NULL"}]}

The Gemini **Express Mode** endpoint (``generativelanguage.googleapis.com``)
does not define that field on its ``FunctionDeclaration`` proto, so it rejects
the entire request::

    400 Invalid JSON payload received. Unknown name "additional_properties" at
    'tools[0].function_declarations[4].parameters.properties[2]...': Cannot
    find field.

Vertex AI tolerates it. That difference is the whole bug: our dev loop is
Vertex, the documented tier-1 onboarding path is Express, so this shipped to a
public template and broke a fork's **first message in every skill** —
``request_confirmation`` is attached to every agent by default.

Design notes
------------

* **Applied to all Gemini requests, not just Express.** Detecting the endpoint
  is possible but fragile (env vars, ADC state, per-member overrides), and a
  mis-detection re-arms the trap silently. The field is meaningless to Vertex
  too — it is ignored there — so unconditional stripping costs nothing and
  removes a whole class of "works on my endpoint" bug.

* **Recursive.** The offending key is nested inside ``any_of`` → ``items``, and
  a future tool could nest it deeper. Walking the whole tree is the only
  version that stays correct.

* **Mutates in place and reports.** The caller logs a one-line summary when
  anything was stripped, so a new offending tool is visible in logs rather than
  silently patched forever.

This is the SECOND provider-side schema constraint found only at a live turn —
``adk/CLAUDE.md`` documents the builtin-tools-cannot-combine-with-function-tools
400. If a third appears, this module is where it belongs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Schema keys the Gemini ``FunctionDeclaration`` proto does not define.
#: Both spellings appear depending on whether the schema has been serialised
#: through the proto (snake_case) or the JSON schema layer (camelCase).
REJECTED_SCHEMA_KEYS: frozenset[str] = frozenset(
    {
        "additional_properties",
        "additionalProperties",
    }
)


def _strip(node: Any, removed: list[str], _depth: int = 0) -> None:
    """Recursively delete rejected keys from a nested schema structure.

    Handles dicts, sequences AND arbitrary objects. The object case is
    load-bearing and was missed on the first pass: ADK's schema is a tree of
    *pydantic models*, not plain dicts, so a dict-only walker strips the top
    level and silently leaves the real occurrence — nested under
    ``any_of -> items`` — in place. The unit tests passed because their
    fixtures were dicts all the way down; only a check against a genuine
    ``_get_declaration()`` caught it.
    """
    if _depth > 40:  # cycle/depth guard; schemas are shallow in practice
        return

    if isinstance(node, dict):
        for key in list(node.keys()):
            if key in REJECTED_SCHEMA_KEYS:
                del node[key]
                removed.append(key)
            else:
                _strip(node[key], removed, _depth + 1)
        return

    if isinstance(node, (list, tuple, set)):
        for item in node:
            _strip(item, removed, _depth + 1)
        return

    # Pydantic model / any object with attributes. Delete the attribute where
    # possible; fall back to setting None when the model forbids deletion.
    inner = getattr(node, "__dict__", None)
    if isinstance(inner, dict):
        for key in list(inner.keys()):
            if key in REJECTED_SCHEMA_KEYS:
                try:
                    delattr(node, key)
                except Exception:
                    setattr(node, key, None)
                removed.append(key)
            else:
                _strip(inner[key], removed, _depth + 1)


def sanitize_function_declarations(llm_request: Any) -> int:
    """Strip rejected keys from every function declaration on ``llm_request``.

    Returns the number of keys removed. Never raises: a malformed or
    unexpected request shape must not break the call — the worst case is the
    original 400, which is what we had before.
    """
    removed: list[str] = []
    try:
        config = getattr(llm_request, "config", None)
        tools = getattr(config, "tools", None) or []
        for tool in tools:
            declarations = getattr(tool, "function_declarations", None) or []
            for declaration in declarations:
                # Walk the WHOLE declaration, not just `parameters`. A tool
                # that RETURNS dict[str, Any] — request_confirmation does —
                # also gets the rejected key on its `response` schema, and
                # sanitizing only the params leaves the request still invalid.
                # Missing that cost a debugging round: the count said 11
                # removed while the serialized declaration was still dirty.
                _strip(declaration, removed)
    except Exception:  # pragma: no cover - defensive; see docstring
        logger.exception("schema_conformance: sanitize failed (continuing unsanitized)")
        return 0

    if removed:
        logger.info(
            "schema_conformance: stripped %d rejected schema key(s) %s — a tool "
            "parameter is typed dict[str, Any]; consider a declared model instead",
            len(removed),
            sorted(set(removed)),
        )
    return len(removed)
