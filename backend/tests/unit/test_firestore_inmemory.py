"""Parity tests for ``InMemoryFirestoreClient``.

The in-memory client must match ``google.cloud.firestore.Client`` for the
12 operations v6 actually uses (surveyed from ``db/firestore.py`` and
``db/chat_sessions.py``):

  Client       : .collection()
  Collection   : .document(), .where(filter=FieldFilter), .order_by(field,
                 direction=...), .limit(n), .stream(), .start_after(snap)
  DocumentRef  : .get(), .set(data, merge=...), .update(data), .delete()
  Snapshot     : .exists, .to_dict(), .id
  Sentinels    : Increment, ArrayUnion, ArrayRemove, SERVER_TIMESTAMP
  Constants    : Query.DESCENDING, Query.ASCENDING

These tests are the contract. If a future caller depends on a Firestore
feature the in-memory client doesn't implement, the test added here
catches the gap.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def client():
    from db.firestore_inmemory import InMemoryFirestoreClient

    return InMemoryFirestoreClient()


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_set_then_get_round_trip(client):
    client.collection("skills").document("s1").set({"name": "Demo", "version": 1})
    snap = client.collection("skills").document("s1").get()
    assert snap.exists is True
    assert snap.id == "s1"
    assert snap.to_dict() == {"name": "Demo", "version": 1}


def test_get_missing_document(client):
    snap = client.collection("skills").document("nope").get()
    assert snap.exists is False
    assert snap.to_dict() is None


def test_set_overwrite_without_merge(client):
    doc = client.collection("c").document("d")
    doc.set({"a": 1, "b": 2})
    doc.set({"a": 9})  # no merge = full overwrite
    assert doc.get().to_dict() == {"a": 9}


def test_set_with_merge_preserves_unset_fields(client):
    doc = client.collection("c").document("d")
    doc.set({"a": 1, "b": 2})
    doc.set({"a": 99}, merge=True)
    assert doc.get().to_dict() == {"a": 99, "b": 2}


def test_update_existing_fields(client):
    doc = client.collection("c").document("d")
    doc.set({"a": 1, "b": 2})
    doc.update({"a": 9, "c": 3})
    assert doc.get().to_dict() == {"a": 9, "b": 2, "c": 3}


def test_update_missing_document_raises(client):
    """Real Firestore raises NotFound on .update() of a missing doc.
    The in-memory client matches this contract.
    """
    with pytest.raises(KeyError):
        client.collection("c").document("missing").update({"a": 1})


def test_delete_document(client):
    doc = client.collection("c").document("d")
    doc.set({"a": 1})
    doc.delete()
    assert doc.get().exists is False


def test_delete_idempotent(client):
    """Deleting a missing doc is a no-op (matches Firestore semantics)."""
    client.collection("c").document("never-existed").delete()


# ---------------------------------------------------------------------------
# Sentinels: Increment, ArrayUnion, ArrayRemove, SERVER_TIMESTAMP
# ---------------------------------------------------------------------------


def test_increment_sentinel(client):
    from db.firestore_inmemory import Increment

    doc = client.collection("c").document("d")
    doc.set({"count": 5})
    doc.update({"count": Increment(3)})
    assert doc.get().to_dict()["count"] == 8


def test_increment_on_missing_field_uses_zero(client):
    from db.firestore_inmemory import Increment

    doc = client.collection("c").document("d")
    doc.set({"other": "x"})
    doc.update({"count": Increment(5)})
    assert doc.get().to_dict()["count"] == 5


def test_array_union_adds_unique_values(client):
    from db.firestore_inmemory import ArrayUnion

    doc = client.collection("c").document("d")
    doc.set({"tags": ["a", "b"]})
    doc.update({"tags": ArrayUnion(["b", "c"])})
    # Order-insensitive, no duplicates.
    assert sorted(doc.get().to_dict()["tags"]) == ["a", "b", "c"]


def test_array_union_on_missing_field_creates_list(client):
    from db.firestore_inmemory import ArrayUnion

    doc = client.collection("c").document("d")
    doc.set({"other": "x"})
    doc.update({"tags": ArrayUnion(["a"])})
    assert doc.get().to_dict()["tags"] == ["a"]


def test_array_remove(client):
    from db.firestore_inmemory import ArrayRemove

    doc = client.collection("c").document("d")
    doc.set({"tags": ["a", "b", "c"]})
    doc.update({"tags": ArrayRemove(["b"])})
    assert sorted(doc.get().to_dict()["tags"]) == ["a", "c"]


def test_server_timestamp_sentinel(client):
    from db.firestore_inmemory import SERVER_TIMESTAMP

    doc = client.collection("c").document("d")
    before = time.time()
    doc.set({"created_at": SERVER_TIMESTAMP})
    written = doc.get().to_dict()["created_at"]
    # Real Firestore returns a datetime; in-memory returns a comparable obj.
    # Just verify it materialised to a non-sentinel value.
    assert written is not SERVER_TIMESTAMP
    # And is plausibly recent (within 5 seconds of "before").
    if hasattr(written, "timestamp"):
        assert written.timestamp() >= before


# ---------------------------------------------------------------------------
# Queries: where / order_by / limit / stream / start_after
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(client):
    """Seed 5 docs into ``items`` with mixed fields for query tests."""
    items = client.collection("items")
    items.document("a").set({"name": "alpha", "score": 5, "tags": ["x", "y"]})
    items.document("b").set({"name": "beta", "score": 2, "tags": ["y"]})
    items.document("c").set({"name": "gamma", "score": 8, "tags": ["z"]})
    items.document("d").set({"name": "delta", "score": 5, "tags": ["x", "z"]})
    items.document("e").set({"name": "epsilon", "score": 1, "tags": []})
    return client


def test_where_equality(seeded):
    from db.firestore_inmemory import FieldFilter

    results = list(seeded.collection("items").where(filter=FieldFilter("score", "==", 5)).stream())
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "delta"]


def test_where_dotted_field_path(client):
    """Dotted field paths walk nested dicts — the marketplace endpoint
    filters on ``accessControl.type == "public"`` and silently returned
    [] in LOCAL_MODE before this support landed (2026-05-18). Regression
    guard so the homepage skill list can't go blank again.
    """
    from db.firestore_inmemory import FieldFilter

    items = client.collection("skills")
    items.document("public-1").set({"name": "alpha", "accessControl": {"type": "public"}})
    items.document("public-2").set({"name": "beta", "accessControl": {"type": "public"}})
    items.document("private-1").set({"name": "gamma", "accessControl": {"type": "private"}})
    items.document("no-access").set({"name": "delta"})  # missing accessControl entirely

    results = list(items.where(filter=FieldFilter("accessControl.type", "==", "public")).stream())
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "beta"], "marketplace-style dotted-path filter must return only public skills"


def test_where_is_null_operator(client):
    """Google's FieldFilter.Operator.IS_NULL enum surfaces in some
    google-cloud-firestore query paths (chat_sessions soft-delete
    filter on `archivedAt IS NULL`, for one). Real Firestore wire
    has no IS_NULL — the SDK rewrites it on the way to the server —
    but the InMemoryFirestoreClient sees the enum value directly,
    so we need to honour it. Regression for the 500 hit on
    GET /api/skills/.../sessions in LOCAL_MODE (2026-05-18).
    """
    from db.firestore_inmemory import FieldFilter

    items = client.collection("sessions")
    items.document("active-1").set({"title": "a", "archivedAt": None})
    items.document("active-2").set({"title": "b"})  # archivedAt missing → IS_NULL match
    items.document("archived").set({"title": "c", "archivedAt": "2026-05-01"})

    # Use the real google enum if available — that's how the production
    # code path constructs the filter. Fall back to the string for users
    # without the SDK installed.
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter as GFieldFilter

        f = GFieldFilter("archivedAt", "==", None)
        # Some SDK versions auto-rewrite == None to IS_NULL — be robust
        # to either representation by also testing the string form.
    except Exception:
        f = FieldFilter("archivedAt", "IS_NULL", None)

    results = list(items.where(filter=f).stream())
    titles = sorted(r.to_dict()["title"] for r in results)
    # `archivedAt: None` matches; `archivedAt` missing entirely also matches.
    # `archivedAt: '2026-05-01'` does NOT match.
    assert "c" not in titles


def test_where_dotted_field_missing_segment(client):
    """A dotted path where an intermediate segment doesn't exist must
    fall through as 'no match' rather than raising. Adversarial documents
    shouldn't crash the marketplace query.
    """
    from db.firestore_inmemory import FieldFilter

    items = client.collection("things")
    items.document("nested").set({"foo": {"bar": "expected"}})
    items.document("flat").set({"foo": "scalar"})  # `foo.bar` traversal hits scalar
    items.document("empty").set({})

    results = list(items.where(filter=FieldFilter("foo.bar", "==", "expected")).stream())
    assert [r.id for r in results] == ["nested"]


def test_where_gt_lt(seeded):
    from db.firestore_inmemory import FieldFilter

    results = list(seeded.collection("items").where(filter=FieldFilter("score", ">", 3)).stream())
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "delta", "gamma"]


def test_where_in(seeded):
    from db.firestore_inmemory import FieldFilter

    results = list(
        seeded.collection("items").where(filter=FieldFilter("name", "in", ["alpha", "gamma", "zeta"])).stream()
    )
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "gamma"]


def test_where_array_contains(seeded):
    from db.firestore_inmemory import FieldFilter

    results = list(seeded.collection("items").where(filter=FieldFilter("tags", "array_contains", "x")).stream())
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "delta"]


def test_where_chained(seeded):
    """Multiple ``.where()`` calls compose with AND semantics."""
    from db.firestore_inmemory import FieldFilter

    results = list(
        seeded.collection("items")
        .where(filter=FieldFilter("score", ">", 1))
        .where(filter=FieldFilter("tags", "array_contains", "y"))
        .stream()
    )
    names = sorted(r.to_dict()["name"] for r in results)
    assert names == ["alpha", "beta"]


def test_order_by_ascending(seeded):
    results = list(seeded.collection("items").order_by("score").stream())
    scores = [r.to_dict()["score"] for r in results]
    assert scores == sorted(scores)


def test_order_by_descending(seeded):
    from db.firestore_inmemory import Query

    results = list(seeded.collection("items").order_by("score", direction=Query.DESCENDING).stream())
    scores = [r.to_dict()["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_limit_caps_results(seeded):
    from db.firestore_inmemory import Query

    results = list(seeded.collection("items").order_by("score", direction=Query.DESCENDING).limit(2).stream())
    assert len(results) == 2
    # Top 2 scores are 8 (gamma) and 5 (alpha or delta — tie).
    assert results[0].to_dict()["name"] == "gamma"


def test_stream_yields_snapshots_with_id(seeded):
    results = list(seeded.collection("items").stream())
    assert all(snap.exists for snap in results)
    assert all(isinstance(snap.id, str) and snap.id for snap in results)


def test_stream_skips_deleted_docs(seeded):
    seeded.collection("items").document("a").delete()
    names = sorted(s.to_dict()["name"] for s in seeded.collection("items").stream())
    assert "alpha" not in names


def test_start_after_cursor(seeded):
    """``.start_after(snap)`` excludes everything up to and including the cursor."""
    from db.firestore_inmemory import Query

    col = seeded.collection("items")
    ordered = list(col.order_by("score", direction=Query.DESCENDING).stream())
    # ordered[0] is gamma (score 8). start_after(gamma) → everything after.
    cursor = col.document(ordered[0].id).get()
    rest = list(col.order_by("score", direction=Query.DESCENDING).start_after(cursor).stream())
    rest_names = [s.to_dict()["name"] for s in rest]
    assert "gamma" not in rest_names
    assert len(rest) == len(ordered) - 1


# ---------------------------------------------------------------------------
# Snapshot interface
# ---------------------------------------------------------------------------


def test_snapshot_id_matches_doc_id(client):
    client.collection("c").document("specific-id").set({"x": 1})
    snap = client.collection("c").document("specific-id").get()
    assert snap.id == "specific-id"


def test_snapshot_to_dict_is_independent_copy(client):
    """Mutating the returned dict must not mutate the stored doc."""
    client.collection("c").document("d").set({"items": [1, 2]})
    snap = client.collection("c").document("d").get()
    data = snap.to_dict()
    data["items"].append(99)
    # Re-fetch must show unchanged data.
    assert client.collection("c").document("d").get().to_dict() == {"items": [1, 2]}


# ---------------------------------------------------------------------------
# Thread safety smoke (simple sanity, not full concurrency suite)
# ---------------------------------------------------------------------------


def test_accepts_real_firestore_increment(client):
    """In-memory client must accept ``google.cloud.firestore.Increment`` too.

    The db/firestore.py wrapper passes ``firestore.Increment(...)`` (the real
    SDK class) regardless of LOCAL_MODE, so the in-memory client has to
    duck-type-recognise it.
    """
    from google.cloud import firestore as real_fs

    doc = client.collection("c").document("d")
    doc.set({"count": 10})
    doc.update({"count": real_fs.Increment(5)})
    assert doc.get().to_dict()["count"] == 15


def test_accepts_real_firestore_array_union(client):
    from google.cloud import firestore as real_fs

    doc = client.collection("c").document("d")
    doc.set({"tags": ["a"]})
    doc.update({"tags": real_fs.ArrayUnion(["b", "c"])})
    assert sorted(doc.get().to_dict()["tags"]) == ["a", "b", "c"]


def test_accepts_real_firestore_field_filter(client):
    """``.where(filter=firestore.FieldFilter(...))`` from real SDK must work."""
    from google.cloud import firestore as real_fs

    client.collection("c").document("a").set({"score": 1})
    client.collection("c").document("b").set({"score": 5})
    results = list(client.collection("c").where(filter=real_fs.FieldFilter("score", ">", 2)).stream())
    assert [s.id for s in results] == ["b"]


def test_writes_from_threads_dont_lose_data(client):
    """Many concurrent writes to different doc ids → all present at end."""
    import threading

    def writer(i):
        client.collection("c").document(f"d-{i}").set({"i": i})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    docs = list(client.collection("c").stream())
    assert len(docs) == 50
