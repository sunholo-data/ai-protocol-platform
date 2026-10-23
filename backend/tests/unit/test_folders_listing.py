"""A folder's documents are a QUERY on `parsed_documents`, not a subcollection.

`list_folder_documents` used to stream `users/{uid}/folders/{fid}/documents`,
which NOTHING has ever written — `ingest_document` puts every document in the
root `parsed_documents` collection tagged with `userId` and `folderId`. So the
listing returned `[]` for folders that genuinely held documents, while
`update_folder_counts` went on incrementing `docCount`: a folder read
"8 documents" and listed none, and an upload that reported `parsed (833 blocks)`
could not be found anywhere in the UI.

Nothing covered this function, which is why it survived. These tests pin the
collection it reads, the filters it applies, and the shape it returns.
"""

from __future__ import annotations

from unittest.mock import patch

from db import folders as folders_db


def _rows() -> list[dict]:
    return [
        {"__id": "old", "originalFilename": "a.docx", "createdAt": "2026-09-01T00:00:00+00:00"},
        {"__id": "new", "originalFilename": "b.docx", "createdAt": "2026-09-11T00:00:00+00:00"},
    ]


def test_reads_parsed_documents_filtered_by_user_and_folder():
    with patch.object(folders_db, "query_documents", return_value=_rows()) as mock_q:
        folders_db.list_folder_documents(user_id="u1", folder_id="f1")

    collection = mock_q.call_args.args[0] if mock_q.call_args.args else mock_q.call_args.kwargs["collection"]
    assert collection == "parsed_documents", (
        f"documents live in parsed_documents, not {collection!r} — a per-folder subcollection is never written"
    )
    assert set(mock_q.call_args.kwargs["filters"]) == {("userId", "==", "u1"), ("folderId", "==", "f1")}


def test_no_order_by_so_no_composite_index_is_required():
    """Equality-only conjunctions are served from single-field indexes. Adding an
    `order_by` here would demand a composite index that is not in
    firestore.indexes.json, and the query would fail only once deployed."""
    with patch.object(folders_db, "query_documents", return_value=[]) as mock_q:
        folders_db.list_folder_documents(user_id="u1", folder_id="f1")

    assert not mock_q.call_args.kwargs.get("order_by")


def test_returns_newest_first_with_an_id_field():
    with patch.object(folders_db, "query_documents", return_value=_rows()):
        docs = folders_db.list_folder_documents(user_id="u1", folder_id="f1")

    assert [d["id"] for d in docs] == ["new", "old"], "sorted here, since the query cannot order"


def test_a_document_with_no_createdAt_still_lists():
    """A record written before createdAt existed must not raise on sort."""
    with patch.object(folders_db, "query_documents", return_value=[{"__id": "x"}, *_rows()]):
        docs = folders_db.list_folder_documents(user_id="u1", folder_id="f1")

    assert {d["id"] for d in docs} == {"x", "new", "old"}


def test_the_phantom_subcollection_helper_is_gone():
    """`_docs_ref` addressed the subcollection nothing writes. Leaving it in place
    is an invitation to wire a second reader to the same empty path."""
    assert not hasattr(folders_db, "_docs_ref")
