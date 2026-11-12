"""A project kept in step with one of the owner's library folders (github-sources.md §10).

Every document in the folder (and, unless switched off, its subfolders) is in the
project; a document that leaves the folder leaves the project. The source tags and
untags, and nothing else: the documents are the owner's, so a folder source never
deletes one, and it untags only what it tagged itself (its manifest), so a document
added to the project by hand stays.

Reading a folder is a few Firestore reads, so it runs on every scheduler tick.
"""

from __future__ import annotations

import time
from typing import Any

import db.folders as folders_db
import db.projects as projects_db
from sources import store
from sources.engine import _REPORT_LIST_MAX

MAX_FOLDER_DOCS = 1000


def folder_doc_ids(uid: str, folder_id: str, include_subfolders: bool) -> list[str] | None:
    """Document ids in the folder tree, or None when the folder is gone."""
    folder = folders_db.get_owned_folder(uid, folder_id)
    if folder is None:
        return None
    ids = [folder["id"]]
    name = str(folder.get("name") or "")
    if include_subfolders and name:
        ids += [f["id"] for f in folders_db.list_folders(uid) if str(f.get("name") or "").startswith(f"{name}/")]
    doc_ids: list[str] = []
    for fid in ids:
        doc_ids += [str(d["id"]) for d in folders_db.list_folder_documents(uid, fid, fields=[]) if d.get("id")]
    return doc_ids


def sync_folder(src: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    uid, project_id = str(src["uid"]), str(src["projectId"])
    report: dict[str, Any] = {
        "dryRun": dry_run,
        "startedAt": store.now_iso(),
        "status": "ok",
        "counts": {"added": 0, "removed": 0, "unchanged": 0, "failed": 0},
        "failed": [],
    }
    current = folder_doc_ids(uid, str(src.get("folderId") or ""), bool(src.get("includeSubfolders", True)))
    if current is None:
        report.update(
            status="failed",
            error={"code": "SOURCE_FOLDER_GONE", "message": "The folder this source follows no longer exists."},
        )
        return _finish(src, report, started, dry_run)
    if len(current) > MAX_FOLDER_DOCS:
        report.update(
            status="failed",
            error={"code": "SOURCE_TOO_LARGE", "message": f"The folder holds more than {MAX_FOLDER_DOCS} documents."},
        )
        return _finish(src, report, started, dry_run)

    owned = store.manifest(src["id"])
    now_in = set(current)
    to_add = [d for d in current if d not in owned]
    to_remove = [d for d in owned if d not in now_in]
    report["counts"]["unchanged"] = len(now_in) - len(to_add)
    if dry_run:
        report["counts"].update(added=len(to_add), removed=len(to_remove))
        return _finish(src, report, started, dry_run)

    for start in range(0, len(to_add), 100):
        batch = to_add[start : start + 100]
        result = projects_db.tag_documents(uid, project_id, batch)
        for doc_id in result.get("added", []):
            store.put_file(src["id"], str(doc_id), blob_sha="", doc_id=str(doc_id), parse_status="")
            report["counts"]["added"] += 1
        # In the project before this source: recorded so it isn't re-tagged every
        # run, and marked so leaving the folder doesn't take it out of the project.
        for doc_id in result.get("already", []):
            store.put_file(
                src["id"], str(doc_id), blob_sha="", doc_id=str(doc_id), parse_status="", extra={"preTagged": True}
            )
            report["counts"]["unchanged"] += 1
        for failure in result.get("failed", []):
            report["counts"]["failed"] += 1
            report["failed"].append(
                {"path": str(failure.get("filename") or failure.get("id")), "error": failure.get("reason", "")}
            )
    for doc_id in to_remove:
        if not owned[doc_id].get("preTagged"):
            projects_db.untag_document(uid, project_id, doc_id)
            report["counts"]["removed"] += 1
        store.drop_file(src["id"], doc_id)
    report["failed"] = report["failed"][:_REPORT_LIST_MAX]
    if report["counts"]["failed"]:
        report["status"] = "partial"
    return _finish(src, report, started, dry_run)


def _finish(src: dict[str, Any], report: dict[str, Any], started: float, dry_run: bool) -> dict[str, Any]:
    report["finishedAt"] = store.now_iso()
    report["seconds"] = round(time.monotonic() - started, 1)
    if not dry_run:
        store.update_source(
            src["id"], {"status": report["status"], "lastReport": report, "syncedAt": report["finishedAt"]}
        )
    return report
