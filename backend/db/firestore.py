"""Standalone Firestore client — no Sunholo dependency.

Provides async-style access to Firestore collections.
Uses the synchronous google-cloud-firestore SDK (no async driver needed
for Cloud Run's concurrency model — one request per container instance).

When ``LOCAL_MODE=1`` is set the client is the in-memory drop-in from
``db.firestore_inmemory`` — no GCP credentials required. See
``config/local_mode.py`` for the flag definition and safety asserts.
"""

from __future__ import annotations

import logging
from typing import Any

from google.cloud import firestore

from config.gcp import resolve_gcp_project
from config.local_mode import is_local_mode

logger = logging.getLogger(__name__)

_client: Any | None = None


def get_client() -> Any:
    """Return a module-level Firestore client (lazy singleton).

    Returns ``InMemoryFirestoreClient`` when LOCAL_MODE is on, else the
    real ``google.cloud.firestore.Client``. The return type is ``Any`` so
    callers don't need to import both; the public API surface is the same.
    """
    global _client
    if _client is None:
        if is_local_mode():
            from db.firestore_inmemory import InMemoryFirestoreClient

            logger.info("LOCAL_MODE=1 — using InMemoryFirestoreClient (no GCP)")
            _client = InMemoryFirestoreClient()
        else:
            project = resolve_gcp_project()
            _client = firestore.Client(project=project) if project else firestore.Client()
    return _client


def _reset_client_for_testing() -> None:
    """Test helper — clears the singleton so tests can flip LOCAL_MODE."""
    global _client
    _client = None


def get_document(collection: str, doc_id: str) -> dict[str, Any] | None:
    """Get a single document by ID. Returns None if not found."""
    doc = get_client().collection(collection).document(doc_id).get()
    return doc.to_dict() if doc.exists else None


def set_document(collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
    """Set (create or overwrite) a document."""
    get_client().collection(collection).document(doc_id).set(data, merge=merge)


def update_document(collection: str, doc_id: str, data: dict[str, Any]) -> None:
    """Update specific fields on an existing document."""
    get_client().collection(collection).document(doc_id).update(data)


def delete_document(collection: str, doc_id: str) -> None:
    """Delete a document by ID."""
    get_client().collection(collection).document(doc_id).delete()


def query_documents(
    collection: str,
    filters: list[tuple[str, str, Any]] | None = None,
    order_by: str | None = None,
    order_direction: str = "DESCENDING",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Query documents with optional filters, ordering, and limit.

    Args:
        collection: Firestore collection path.
        filters: List of (field, op, value) tuples. Op is a Firestore operator
                 string like "==", "in", "array_contains".
        order_by: Field to order results by.
        order_direction: "ASCENDING" or "DESCENDING".
        limit: Max documents to return.

    Returns:
        List of document dicts (each includes the document ID as "__id").
    """
    ref = get_client().collection(collection)
    query = ref

    if filters:
        for field, op, value in filters:
            query = query.where(filter=firestore.FieldFilter(field, op, value))

    if order_by:
        direction = firestore.Query.DESCENDING if order_direction == "DESCENDING" else firestore.Query.ASCENDING
        query = query.order_by(order_by, direction=direction)

    if limit:
        query = query.limit(limit)

    results = []
    for doc in query.stream():
        data = doc.to_dict()
        if data is not None:
            data["__id"] = doc.id
            results.append(data)
    return results


def increment_field(collection: str, doc_id: str, field: str, amount: int = 1) -> None:
    """Atomically increment a numeric field."""
    get_client().collection(collection).document(doc_id).update({field: firestore.Increment(amount)})
