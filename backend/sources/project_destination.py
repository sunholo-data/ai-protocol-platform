"""Project destination: a source's files become library documents in a project
(github-sources.md §4-§5).

A file at ``sessions/02/notes.md`` under a source whose folder prefix is
``client-a`` lands in library folder ``client-a/sessions/02`` as ``notes.md`` —
the same folder names ``aiplatform docs upload -r`` gives a checkout of that
path, which is what lets a first sync adopt an earlier upload in place.

Every write goes through ``ingest_document`` — the only way into the library — so
a synced document is versioned, parsed, tagged into the project and indexed
exactly as an upload is.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException

import db.folders as folders_db
from auth import User
from db.firestore import get_document, query_documents, update_document
from sources.engine import PutResult
from tools.documents.ingest import _ALLOWED_EXTENSIONS, SOURCE_TEXT_EXTENSIONS, ingest_document

log = logging.getLogger(__name__)

_DOCS = "parsed_documents"


class ProjectDestination:
    def __init__(self, user: User, project_id: str, folder_prefix: str) -> None:
        self._user = user
        self._project_id = project_id
        self._prefix = folder_prefix.strip("/")
        self._folder_ids: dict[str, str] | None = None

    def placement(self, path: str) -> tuple[str, str]:
        """(library folder name, filename) for a source-relative path."""
        rel = PurePosixPath(path)
        folder = "/".join(p for p in (self._prefix, *rel.parent.parts) if p and p != ".")
        return folder, rel.name

    def accepts(self, path: str) -> str | None:
        ext = PurePosixPath(path).suffix.lower()
        if not ext:
            return "no file extension"
        if ext in _ALLOWED_EXTENSIONS or ext in SOURCE_TEXT_EXTENSIONS:
            return None
        return f"type {ext} is not supported"

    def _folder_id(self, name: str) -> str | None:
        # Read once per run. A folder ingest creates later in the run is new, so
        # nothing can already sit in it: missing it here classifies correctly.
        if self._folder_ids is None:
            self._folder_ids = {
                str(f.get("name") or "").strip().casefold(): str(f["id"])
                for f in folders_db.list_folders(self._user.uid)
            }
        return self._folder_ids.get(name.strip().casefold())

    def _existing_id(self, path: str) -> str | None:
        folder_name, filename = self.placement(path)
        folder_id = self._folder_id(folder_name) if folder_name else None
        if folder_id is None:
            return None
        rows = query_documents(
            _DOCS,
            filters=[
                ("userId", "==", self._user.uid),
                ("folderId", "==", folder_id),
                ("originalFilename", "==", filename),
            ],
            limit=1,
        )
        return str(rows[0]["__id"]) if rows else None

    def exists(self, path: str) -> bool:
        return self._existing_id(path) is not None

    async def put(self, path: str, data: bytes, source_ref: dict[str, Any]) -> PutResult:
        folder_name, filename = self.placement(path)
        existed = self._existing_id(path) is not None
        try:
            resp = await ingest_document(
                user=self._user,
                file_bytes=data,
                filename=filename,
                folder_path=folder_name,
                skip_unchanged=True,
                project_id=self._project_id,
                source="git",
                source_ref=source_ref,
            )
        except HTTPException as exc:
            return PutResult(outcome="failed", error=str(exc.detail))
        except Exception as exc:
            log.exception("source file %s: ingest failed", path)
            return PutResult(outcome="failed", error=str(exc)[:300])
        if resp.unchanged:
            # Identical bytes: ingest returned the existing record without
            # rewriting it, so stamp the provenance an adoption needs here.
            try:
                update_document(_DOCS, resp.doc_id, {"source": "git", "sourceRef": source_ref})
            except Exception as exc:
                log.warning("source file %s: could not record provenance on %s: %s", path, resp.doc_id, exc)
            outcome = "unchanged"
        else:
            outcome = "updated" if existed else "added"
        if resp.project_error:
            return PutResult(doc_id=resp.doc_id, outcome="failed", error=resp.project_error)
        return PutResult(doc_id=resp.doc_id, outcome=outcome, parse_status=resp.status, error=resp.error or "")

    def move(self, doc_id: str, path: str, source_ref: dict[str, Any]) -> PutResult:
        """Move this document to where ``path`` places it, keeping its id, its
        parsed text and its chats. Blocking (Firestore, and the stored object).
        ``failed`` when it can't (gone, or the name is taken there): the engine
        then adds the file anew."""
        from tools.documents.routes import _move_document, _rename_document

        doc = get_document(_DOCS, doc_id)
        if doc is None or doc.get("userId") != self._user.uid:
            return PutResult(outcome="failed", error="the document is gone")
        folder_name, filename = self.placement(path)
        if not folder_name:
            return PutResult(outcome="failed", error="no folder to move into")
        try:
            folder_id = folders_db.ensure_named_folder(self._user.uid, folder_name)
            self._folder_ids = None  # it may be new
            if doc.get("folderId") != folder_id:
                doc = _move_document(doc_id, doc, folder_id, False, self._user)
            if doc.get("originalFilename") != filename:
                doc = _rename_document(doc_id, doc, filename, self._user)
            update_document(_DOCS, doc_id, {"source": "git", "sourceRef": source_ref})
        except HTTPException as exc:
            return PutResult(doc_id=doc_id, outcome="failed", error=str(exc.detail))
        return PutResult(doc_id=doc_id, outcome="moved", parse_status=str(doc.get("parseStatus") or ""))

    def remove(self, doc_id: str) -> bool:
        doc = get_document(_DOCS, doc_id)
        if doc is None or doc.get("userId") != self._user.uid:
            return False
        from tools.documents.routes import _delete_owned_document

        _delete_owned_document(doc_id, doc, self._user)
        return True
