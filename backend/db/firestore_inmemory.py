"""In-memory Firestore client — drop-in replacement for
``google.cloud.firestore.Client`` for the surface v6 actually uses.

Implements the contract pinned by tests/unit/test_firestore_inmemory.py:

  Client       : .collection()
  Collection   : .document(), .where(filter=FieldFilter), .order_by(field,
                 direction=...), .limit(n), .stream(), .start_after(snap)
  DocumentRef  : .get(), .set(data, merge=...), .update(data), .delete()
  Snapshot     : .exists, .to_dict(), .id
  Sentinels    : Increment, ArrayUnion, ArrayRemove, SERVER_TIMESTAMP
  Constants    : Query.DESCENDING, Query.ASCENDING

Storage is a nested dict ``{collection: {doc_id: data}}`` under a single
``threading.RLock``. The lock is conservative (one lock for all collections)
because workshop fixtures are tiny — correctness wins over contention.

The behaviour diverges from real Firestore in a few well-bounded ways:
- No transactions. v6 doesn't use them.
- Range and inequality queries on multiple fields are not validated
  against Firestore's single-inequality-per-query rule.
- ``Query.ASCENDING`` / ``DESCENDING`` are direction strings (not enum
  values) — matches how callers pass them.

If a new caller depends on a Firestore feature missing here, add a test
to ``test_firestore_inmemory.py`` first, then add the implementation.
"""

from __future__ import annotations

import copy
import datetime
import logging
import operator as _op
import threading
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------


class _ServerTimestampSentinel:
    """Marker class for SERVER_TIMESTAMP. Resolved to ``datetime.now(UTC)``
    at write time.
    """

    def __repr__(self) -> str:
        return "SERVER_TIMESTAMP"


SERVER_TIMESTAMP = _ServerTimestampSentinel()


class Increment:
    """Atomic numeric increment sentinel. Matches firestore.Increment."""

    __slots__ = ("amount",)

    def __init__(self, amount: int | float):
        self.amount = amount


class ArrayUnion:
    """Atomic set-union for list fields. Matches firestore.ArrayUnion."""

    __slots__ = ("values",)

    def __init__(self, values: Iterable[Any]):
        self.values = list(values)


class ArrayRemove:
    """Atomic remove from list fields. Matches firestore.ArrayRemove."""

    __slots__ = ("values",)

    def __init__(self, values: Iterable[Any]):
        self.values = list(values)


# ---------------------------------------------------------------------------
# FieldFilter + Query direction constants
# ---------------------------------------------------------------------------


class FieldFilter:
    """Minimal stand-in for ``google.cloud.firestore.FieldFilter``.

    Stores ``(field, op, value)`` and exposes them via attributes the query
    engine reads. Operators are the same string set Firestore accepts —
    we implement the subset v6 actually uses.
    """

    __slots__ = ("field", "op", "value")

    def __init__(self, field: str, op: str, value: Any):
        self.field = field
        self.op = op
        self.value = value


class Query:
    """Namespace for direction constants. Mirrors firestore.Query.

    Values are direction strings rather than enum members so they survive
    YAML/JSON serialization in tests and config.
    """

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


_OPERATORS: dict[Any, Any] = {
    "==": _op.eq,
    "!=": _op.ne,
    "<": _op.lt,
    "<=": _op.le,
    ">": _op.gt,
    ">=": _op.ge,
    "in": lambda field_val, allowed: field_val in allowed,
    "not-in": lambda field_val, blocked: field_val not in blocked,
    "array_contains": lambda field_val, needle: isinstance(field_val, list) and needle in field_val,
    "array_contains_any": lambda field_val, needles: (
        isinstance(field_val, list) and any(n in field_val for n in needles)
    ),
    # Null-check operators emitted by some Firestore query builders
    # (e.g. archivedAt IS NULL for soft-deleted-row exclusion). Real
    # Firestore lacks a literal IS_NULL operator on the wire, but the
    # Python SDK Query API translates these via `where(field == None)`.
    # The InMemoryFirestoreClient sees the enum directly; map it here
    # so chat_sessions queries (sprint 1.13/14/15) work in LOCAL_MODE.
    "IS_NULL": lambda field_val, _ignored: field_val is None,
    "IS_NOT_NULL": lambda field_val, _ignored: field_val is not None,
}


def _normalise_op(op: Any) -> Any:
    """Convert google's FieldFilter.Operator enum (or any object with
    a `.name`) to the string keys used in `_OPERATORS`. Returns the
    original object if it's already a string or unknown shape.
    """
    if isinstance(op, str):
        return op
    name = getattr(op, "name", None)
    if name in {"IS_NULL", "IS_NOT_NULL"}:
        return name
    return op


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class _Snapshot:
    """Document snapshot returned by ``.get()`` and ``.stream()``."""

    __slots__ = ("_collection", "_data", "_doc_id", "exists", "id")

    def __init__(self, collection: str, doc_id: str, data: dict[str, Any] | None):
        # _data holds a deep-copy so consumers can't mutate the store.
        self._data = copy.deepcopy(data) if data is not None else None
        self.id = doc_id
        self.exists = data is not None
        self._collection = collection
        self._doc_id = doc_id

    def to_dict(self) -> dict[str, Any] | None:
        # Re-deepcopy so successive callers can't share mutation state.
        if self._data is None:
            return None
        return copy.deepcopy(self._data)


# ---------------------------------------------------------------------------
# Document reference
# ---------------------------------------------------------------------------


class _DocumentRef:
    __slots__ = ("_client", "_collection", "id")

    def __init__(self, client: InMemoryFirestoreClient, collection: str, doc_id: str):
        self._client = client
        self._collection = collection
        self.id = doc_id

    def get(self) -> _Snapshot:
        with self._client._lock:
            data = self._client._store.get(self._collection, {}).get(self.id)
        return _Snapshot(self._collection, self.id, data)

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        resolved = _resolve_writes_for_set(data)
        with self._client._lock:
            col = self._client._store.setdefault(self._collection, {})
            if merge and self.id in col:
                existing = col[self.id]
                existing.update(resolved)
            else:
                col[self.id] = resolved

    def update(self, data: dict[str, Any]) -> None:
        with self._client._lock:
            col = self._client._store.setdefault(self._collection, {})
            if self.id not in col:
                raise KeyError(f"NotFound: cannot update non-existent document {self._collection}/{self.id}")
            existing = col[self.id]
            for field, raw_value in data.items():
                existing[field] = _apply_sentinel(existing.get(field), raw_value)

    def delete(self) -> None:
        with self._client._lock:
            col = self._client._store.get(self._collection)
            if col is not None:
                col.pop(self.id, None)


def _resolve_writes_for_set(data: dict[str, Any]) -> dict[str, Any]:
    """For ``.set()``, sentinels resolve as if the prior value is missing."""
    return {k: _apply_sentinel(None, v) for k, v in data.items()}


def _apply_sentinel(prior: Any, raw: Any) -> Any:
    """Resolve a sentinel against a prior value, or return raw unchanged.

    SERVER_TIMESTAMP -> current UTC datetime
    Increment        -> (prior or 0) + amount
    ArrayUnion       -> sorted set-union with prior list
    ArrayRemove      -> prior list with values removed

    Accepts the in-memory sentinel classes AND the real google.cloud.firestore
    sentinels (``firestore.Increment``, ``firestore.ArrayUnion``,
    ``firestore.ArrayRemove``, ``firestore.SERVER_TIMESTAMP``) so callers can
    pass either kind without branching on LOCAL_MODE.
    """
    # SERVER_TIMESTAMP — match our sentinel OR the google sentinel class.
    if isinstance(raw, _ServerTimestampSentinel):
        return datetime.datetime.now(datetime.UTC)
    if type(raw).__name__ == "Sentinel" and "SERVER_TIMESTAMP" in repr(raw):
        return datetime.datetime.now(datetime.UTC)

    # Increment — our class or google.cloud.firestore.Increment.
    if isinstance(raw, Increment):
        base = prior if isinstance(prior, (int, float)) else 0
        return base + raw.amount
    if hasattr(raw, "_value") and type(raw).__name__ in {"Increment", "_NumericValue"}:
        base = prior if isinstance(prior, (int, float)) else 0
        return base + raw._value  # type: ignore[attr-defined]

    # ArrayUnion — our class or google.cloud.firestore.ArrayUnion.
    if isinstance(raw, ArrayUnion):
        return _union(prior, raw.values)
    if type(raw).__name__ == "ArrayUnion" and hasattr(raw, "values"):
        return _union(prior, raw.values)

    # ArrayRemove — our class or google.cloud.firestore.ArrayRemove.
    if isinstance(raw, ArrayRemove):
        return _remove(prior, raw.values)
    if type(raw).__name__ == "ArrayRemove" and hasattr(raw, "values"):
        return _remove(prior, raw.values)

    return raw


def _union(prior: Any, values: Iterable[Any]) -> list[Any]:
    existing = list(prior) if isinstance(prior, list) else []
    for v in values:
        if v not in existing:
            existing.append(v)
    return existing


def _remove(prior: Any, values: Iterable[Any]) -> list[Any]:
    existing = list(prior) if isinstance(prior, list) else []
    block = list(values)
    return [v for v in existing if v not in block]


# ---------------------------------------------------------------------------
# Query (collection + composable filters + ordering + limit + cursor)
# ---------------------------------------------------------------------------


class _Query:
    """Composable query against a single collection.

    Each chainable method returns a NEW ``_Query`` with the constraint added,
    so the original collection ref is reusable (mirrors real Firestore).
    """

    __slots__ = ("_client", "_collection", "_filters", "_limit", "_order_by", "_start_after")

    def __init__(
        self,
        client: InMemoryFirestoreClient,
        collection: str,
        filters: list[FieldFilter] | None = None,
        order_by: list[tuple[str, str]] | None = None,
        limit: int | None = None,
        start_after: _Snapshot | None = None,
    ):
        self._client = client
        self._collection = collection
        self._filters = list(filters or [])
        self._order_by = list(order_by or [])
        self._limit = limit
        self._start_after = start_after

    def where(self, *, filter: FieldFilter | None = None, **_kwargs: Any) -> _Query:
        if filter is None:
            raise TypeError("where() requires a keyword 'filter=FieldFilter(...)'")
        return _Query(
            self._client,
            self._collection,
            filters=[*self._filters, filter],
            order_by=self._order_by,
            limit=self._limit,
            start_after=self._start_after,
        )

    def order_by(self, field: str, direction: str = Query.ASCENDING) -> _Query:
        return _Query(
            self._client,
            self._collection,
            filters=self._filters,
            order_by=[*self._order_by, (field, direction)],
            limit=self._limit,
            start_after=self._start_after,
        )

    def limit(self, n: int) -> _Query:
        return _Query(
            self._client,
            self._collection,
            filters=self._filters,
            order_by=self._order_by,
            limit=n,
            start_after=self._start_after,
        )

    def start_after(self, snapshot: _Snapshot) -> _Query:
        return _Query(
            self._client,
            self._collection,
            filters=self._filters,
            order_by=self._order_by,
            limit=self._limit,
            start_after=snapshot,
        )

    def stream(self) -> Iterable[_Snapshot]:
        with self._client._lock:
            col_data = dict(self._client._store.get(self._collection, {}))

        # 1. Filter.
        rows: list[tuple[str, dict[str, Any]]] = []
        for doc_id, data in col_data.items():
            if all(_match_filter(data, f) for f in self._filters):
                rows.append((doc_id, data))

        # 2. Order.
        for field, direction in reversed(self._order_by):
            reverse = direction == Query.DESCENDING
            rows.sort(
                key=lambda kv, f=field: _sort_key(kv[1].get(f)),
                reverse=reverse,
            )

        # 3. start_after cursor — drop everything up to and including the cursor id.
        if self._start_after is not None:
            cursor_id = self._start_after.id
            cut_index: int | None = None
            for i, (doc_id, _data) in enumerate(rows):
                if doc_id == cursor_id:
                    cut_index = i
                    break
            if cut_index is not None:
                rows = rows[cut_index + 1 :]

        # 4. Limit.
        if self._limit is not None:
            rows = rows[: self._limit]

        for doc_id, data in rows:
            yield _Snapshot(self._collection, doc_id, data)


def _resolve_field_path(data: dict[str, Any], field_path: str) -> tuple[bool, Any]:
    """Walk a dotted Firestore field path (e.g. ``accessControl.type``) through
    nested dicts. Returns ``(found, value)`` so callers can distinguish
    "key missing" from "key present with value ``None``".

    Real Firestore supports field paths natively in `FieldFilter`. Our
    in-memory client missed this for months — the marketplace endpoint
    ``list_marketplace()`` filters on ``accessControl.type == "public"`` and
    silently returned 0 results in LOCAL_MODE because the literal key
    ``"accessControl.type"`` never existed at the top of the document.
    Spotted 2026-05-18 when the homepage skill list went blank.
    """
    cur: Any = data
    for segment in field_path.split("."):
        if not isinstance(cur, dict) or segment not in cur:
            return False, None
        cur = cur[segment]
    return True, cur


def _match_filter(data: dict[str, Any], f: Any) -> bool:
    """Match either our in-memory FieldFilter (.field/.op) or google's
    ``firestore.FieldFilter`` (.field_path/.op_string). Both expose ``.value``.

    Supports dotted field paths via :func:`_resolve_field_path`.
    """
    field = getattr(f, "field", None) or getattr(f, "field_path", None)
    op = _normalise_op(getattr(f, "op", None) or getattr(f, "op_string", None))
    value = f.value
    op_fn = _OPERATORS.get(op)
    if op_fn is None:
        raise ValueError(f"unsupported FieldFilter operator: {op!r}")
    found, actual = _resolve_field_path(data, field)
    if not found and op not in {"!=", "not-in"}:
        return False
    return bool(op_fn(actual, value))


def _sort_key(value: Any) -> tuple[int, Any]:
    """Sortable key that places ``None`` first to mimic Firestore's behaviour."""
    if value is None:
        return (0, 0)
    return (1, value)


# ---------------------------------------------------------------------------
# Collection reference
# ---------------------------------------------------------------------------


class _CollectionRef(_Query):
    """A collection is a query with no constraints yet, plus ``.document()``."""

    __slots__ = ()

    def __init__(self, client: InMemoryFirestoreClient, collection: str):
        super().__init__(client, collection)

    def document(self, doc_id: str | None = None) -> _DocumentRef:
        if doc_id is None:
            import uuid

            doc_id = uuid.uuid4().hex
        return _DocumentRef(self._client, self._collection, doc_id)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class InMemoryFirestoreClient:
    """Drop-in replacement for ``google.cloud.firestore.Client``."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def collection(self, name: str) -> _CollectionRef:
        return _CollectionRef(self, name)

    # ------------------------------------------------------------------ debug

    def snapshot_size(self) -> dict[str, int]:
        """Return a per-collection document count. Handy for fixture asserts."""
        with self._lock:
            return {k: len(v) for k, v in self._store.items()}

    def clear(self) -> None:
        """Reset the entire store. Test helper only."""
        with self._lock:
            self._store.clear()
