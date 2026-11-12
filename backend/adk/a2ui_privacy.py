"""Reserved browser-only A2UI namespace; reject export before ADK state."""

from typing import Any


def public_surface_snapshot(snapshot: Any) -> dict[str, Any]:
    """Drop private display surfaces from an ordinary/model-triggered turn.

    The browser registry already excludes these. Repeat at the server boundary
    for stale clients and model-facing summaries. Never store private payloads
    in a session and try to redact them afterwards.
    """
    if not isinstance(snapshot, dict):
        return {}
    return {k: v for k, v in snapshot.items() if isinstance(k, str) and not k.startswith("private:")}
