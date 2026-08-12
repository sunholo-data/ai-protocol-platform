"""Pre-defined JSON schemas for structured data extraction.

Ported from v5 tools/schemas/__init__.py — JSON Schema format, not Pydantic.
These dicts are passed directly to structured_extraction_callback via
app:extraction_schema session state.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMAS: dict[str, dict[str, Any]] = {
    "summary": {
        "type": "object",
        "description": "A structured summary of the document or response",
        "properties": {
            "main_topic": {"type": "string", "description": "The primary topic or subject discussed"},
            "summary": {"type": "string", "description": "A concise 1-2 sentence summary"},
            "key_points": {
                "type": "array",
                "description": "List of the most important points",
                "items": {"type": "string"},
            },
            "confidence_score": {
                "type": "number",
                "description": "Confidence level 0.0-1.0",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "propertyOrdering": ["main_topic", "summary", "key_points", "confidence_score"],
        "required": ["summary", "key_points"],
    },
    "entities": {
        "type": "object",
        "description": "Extract named entities and concepts",
        "properties": {
            "people": {"type": "array", "items": {"type": "string"}},
            "organizations": {"type": "array", "items": {"type": "string"}},
            "locations": {"type": "array", "items": {"type": "string"}},
            "dates_times": {"type": "array", "items": {"type": "string"}},
            "concepts": {"type": "array", "items": {"type": "string"}},
            "urls": {"type": "array", "items": {"type": "string"}},
        },
        "propertyOrdering": ["people", "organizations", "locations", "dates_times", "concepts", "urls"],
    },
    "action_items": {
        "type": "object",
        "description": "Extract actionable tasks and next steps",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                        "category": {"type": "string"},
                        "deadline": {"type": "string"},
                    },
                    "required": ["description", "priority"],
                },
            },
            "follow_up_questions": {"type": "array", "items": {"type": "string"}},
            "decisions_needed": {"type": "array", "items": {"type": "string"}},
        },
        "propertyOrdering": ["tasks", "follow_up_questions", "decisions_needed"],
    },
    "invoice": {
        "type": "object",
        "description": "Extract structured data from an invoice",
        "properties": {
            "invoice_number": {"type": "string"},
            "invoice_date": {"type": "string"},
            "vendor_name": {"type": "string"},
            "customer_name": {"type": "string"},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit_price": {"type": "string"},
                        "total_price": {"type": "string"},
                    },
                    "required": ["description"],
                },
            },
            "subtotal": {"type": "string"},
            "tax_amount": {"type": "string"},
            "total_amount": {"type": "string"},
            "currency": {"type": "string"},
            "due_date": {"type": "string"},
        },
        "propertyOrdering": [
            "invoice_number",
            "invoice_date",
            "vendor_name",
            "customer_name",
            "line_items",
            "subtotal",
            "tax_amount",
            "total_amount",
            "currency",
            "due_date",
        ],
        "required": ["invoice_number", "vendor_name", "total_amount", "currency"],
    },
    "contract": {
        "type": "object",
        "description": "Extract key terms and obligations from a contract",
        "properties": {
            "contract_type": {"type": "string"},
            "parties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            "effective_date": {"type": "string"},
            "expiry_date": {"type": "string"},
            "governing_law": {"type": "string"},
            "key_obligations": {"type": "array", "items": {"type": "string"}},
            "payment_terms": {"type": "string"},
            "termination_conditions": {"type": "array", "items": {"type": "string"}},
            "notices": {"type": "string"},
        },
        "propertyOrdering": [
            "contract_type",
            "parties",
            "effective_date",
            "expiry_date",
            "governing_law",
            "key_obligations",
            "payment_terms",
            "termination_conditions",
            "notices",
        ],
        "required": ["contract_type", "parties"],
    },
    "meeting_minutes": {
        "type": "object",
        "description": "Extract structured data from meeting minutes",
        "properties": {
            "meeting_date": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "agenda_items": {"type": "array", "items": {"type": "string"}},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "owner": {"type": "string"},
                        "due_date": {"type": "string"},
                    },
                    "required": ["task"],
                },
            },
            "next_meeting": {"type": "string"},
        },
        "propertyOrdering": [
            "meeting_date",
            "attendees",
            "agenda_items",
            "decisions",
            "action_items",
            "next_meeting",
        ],
        "required": ["meeting_date", "decisions"],
    },
}


def get_schema(name: str) -> dict[str, Any]:
    """Return a pre-defined schema by name.

    Raises:
        ValueError: If the name is not in SCHEMAS.
    """
    if name not in SCHEMAS:
        available = ", ".join(sorted(SCHEMAS))
        raise ValueError(f"Unknown schema {name!r}. Available: {available}")
    return SCHEMAS[name]


def list_schemas() -> list[str]:
    """Return all available pre-defined schema names."""
    return sorted(SCHEMAS)


def load_schema_from_file(file_path: str) -> dict[str, Any]:
    """Load a JSON Schema from a file."""
    with open(file_path) as f:
        return json.load(f)
