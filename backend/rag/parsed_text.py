"""Import the authorised canonical text into RAG, without re-parsing the source.

Temporary files are private and removed even on failure. A per-document receipt
in the user's backend-only profile avoids re-uploading unchanged text across
sessions. A changed text version replaces its previous RAG file.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from db.firestore import get_document, set_document
from rag.corpus import _delete_file_sync, _upload_file_sync
from tools.documents.parsed_text import load_parsed_text

MAX_TEXT_BYTES = 10 * 1024 * 1024


@lru_cache(maxsize=512)
def _import_lock(key: str) -> threading.Lock:
    return threading.Lock()


def _import_sync(corpus_name: str, doc_id: str, caller_uid: str) -> str:
    parsed = load_parsed_text(doc_id, caller_uid=caller_uid)
    if len(parsed.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("The parsed text exceeds the RAG text-file limit. Split it before indexing.")
    collection = f"user_profiles/{caller_uid}/parsed_text_imports"
    key = hashlib.sha256(f"{corpus_name}|{doc_id}".encode()).hexdigest()
    with _import_lock(key):
        receipt = get_document(collection, key) or {}
        if (
            receipt.get("fingerprint") == parsed.fingerprint
            and receipt.get("filename") == parsed.filename
            and receipt.get("fileName")
        ):
            return parsed.fingerprint
        if receipt.get("fileName"):
            # Do not retrieve stale clauses alongside an edited version. If the
            # replacement fails, the caller reports degraded search and retries.
            _delete_file_sync(receipt["fileName"])
            set_document(collection, key, {"fileName": None}, merge=True)
        with tempfile.TemporaryDirectory(prefix="rag-parsed-text-") as directory:
            path = Path(directory) / "parsed.txt"
            path.write_text(parsed.text, encoding="utf-8")
            display_name = f"{parsed.filename[:104]} (parsed text).txt"
            uploaded = _upload_file_sync(corpus_name, str(path), display_name)
        set_document(
            collection,
            key,
            {
                "docId": doc_id,
                "corpusName": corpus_name,
                "fingerprint": parsed.fingerprint,
                "fileName": uploaded.name,
                "representation": "parsed-text-v1",
                "filename": parsed.filename,
            },
        )
    return parsed.fingerprint


async def import_parsed_document(corpus_name: str, doc_id: str, *, caller_uid: str) -> str:
    """Import/reuse one successfully parsed document, returning its text hash."""
    return await asyncio.to_thread(_import_sync, corpus_name, doc_id, caller_uid)
