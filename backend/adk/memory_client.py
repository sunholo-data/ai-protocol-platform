"""Memory Bank client surfaces across the Vertex and Agent Platform SDKs."""

from typing import Any


def memories_api(client: Any) -> Any:
    """CRUD/retrieval API; SDK 2 exposes it under memory_banks."""
    banks = getattr(client, "memory_banks", None)
    return banks.memories if banks is not None else client.agent_engines.memories


def ingest_events_api(client: Any) -> Any:
    """Ingest lives on memory_banks itself in the new SDK, not memories."""
    banks = getattr(client, "memory_banks", None)
    return banks.ingest_events if banks is not None else client.agent_engines.memories.ingest_events
