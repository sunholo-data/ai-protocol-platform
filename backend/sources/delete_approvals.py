"""The project's AI asks to delete files in a writable source; the owner decides
(project-source-write.md W7, through human-approvals.md).

``prepare`` checks the request exactly as a write is checked (the folder is
writable, the person may still write, each file is one the project holds) and
stores it as a pending approval: nothing touches GitHub yet. The owner decides
on the approval page (``/approve/source-delete:<ref>``), or from the CLI or an
MCP client with approval rights. ``SourceDeleteApprovals.decide`` then re-checks
everything, deletes the files as ONE commit (refused if any of them changed on
GitHub meanwhile) and removes their documents from the project.

The review text is written here from the stored request, never by a model; the
agent's commit message is shown as a quotation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

from adk.approvals import ApprovalRequest, Approver, Receipt
from db.firestore import get_document, query_documents, set_document, update_document
from sources import credentials, git_write, store
from sources.github import GitHubError
from sources.project_destination import ProjectDestination
from sources.project_write import (
    MAX_FILES,
    WriteRefused,
    _owner,
    _trailers,
    _writer,
    folder_of,
    locate,
    repo_path,
    writable_sources,
)

log = logging.getLogger(__name__)

KIND = "source-delete"
COLLECTION = "source_delete_requests"
TTL_SECONDS = 24 * 3600


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def approval_url(approval_id: str) -> str:
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/approve/{approval_id}"


async def prepare(uid: str, project_id: str, paths: list[str], message: str, *, session_id: str = "") -> dict[str, Any]:
    """Store a request to delete ``paths`` (as the project shows them); returns
    where the owner approves it. Raises ``WriteRefused``."""
    message = (message or "").strip()
    if not message:
        raise WriteRefused("SOURCE_WRITE_INVALID", "Give a commit message saying what is deleted and why.")
    if not paths:
        raise WriteRefused("SOURCE_WRITE_INVALID", "Name at least one file to delete.")
    if len(paths) > MAX_FILES:
        raise WriteRefused("SOURCE_WRITE_INVALID", f"At most {MAX_FILES} files in one request.")
    sources = await asyncio.to_thread(writable_sources, uid, project_id)
    if not sources:
        raise WriteRefused("SOURCE_WRITE_NONE", "This project has no folders the AI may write to.")
    src: dict[str, Any] | None = None
    rels: list[str] = []
    for path in paths:
        where, rel = locate(sources, str(path or ""))
        if src is not None and where["id"] != src["id"]:
            raise WriteRefused("SOURCE_WRITE_INVALID", "One request deletes from one folder. Split it by folder.")
        src = where
        if rel not in rels:
            rels.append(rel)
    assert src is not None
    owner = await asyncio.to_thread(_owner, uid)
    _writer(src, owner)
    manifest = await asyncio.to_thread(store.manifest, src["id"])
    missing = [f"{folder_of(src)}/{rel}" for rel in rels if not (manifest.get(rel) or {}).get("blobSha")]
    if missing:
        raise WriteRefused(
            "SOURCE_WRITE_INVALID", "Not files this project holds from GitHub: " + ", ".join(missing) + "."
        )
    repo, branch = str(src["repo"]), str(src["ref"])
    payload = {
        "sourceId": src["id"],
        "repo": repo,
        "branch": branch,
        "paths": sorted(rels),
        "expected": {rel: manifest[rel]["blobSha"] for rel in sorted(rels)},
        "message": message,
    }
    ref = uuid.uuid4().hex
    folder = folder_of(src)
    listing = "\n".join(f"- {folder}/{rel}" for rel in sorted(rels))
    review = (
        f"Delete {len(rels)} file{'s' if len(rels) != 1 else ''} from {repo} (branch {branch}) in one commit, "
        f"and remove {'them' if len(rels) != 1 else 'it'} from this project:\n{listing}\n\n"
        f'The AI\'s commit message: "{message}"\n\n'
        "A file changed on GitHub since the project last synced is not deleted; the whole request is refused."
    )
    now = time.time()
    row = {
        **payload,
        "ownerUid": uid,
        "projectId": project_id,
        "sessionId": session_id,
        "title": f"Delete {len(rels)} file{'s' if len(rels) != 1 else ''} from {repo}",
        "review": review,
        "digest": _digest(payload),
        "state": "pending",
        "createdAt": now,
        "expiresAt": now + TTL_SECONDS,
    }
    await asyncio.to_thread(set_document, COLLECTION, ref, row)
    approval_id = f"{KIND}:{ref}"
    log.info("project %s: delete of %d file(s) in %s waits for approval %s", project_id, len(rels), repo, approval_id)
    return {
        "status": "needs_approval",
        "approval_id": approval_id,
        "approve_url": approval_url(approval_id),
        "title": row["title"],
        "files": [f"{folder}/{rel}" for rel in sorted(rels)],
        "message": "Nothing is deleted until the person approves it at approve_url.",
    }


def _request(ref: str, row: dict[str, Any]) -> ApprovalRequest:
    state = str(row.get("state") or "pending")
    if state == "pending" and float(row.get("expiresAt") or 0) <= time.time():
        state = "expired"
    return ApprovalRequest(
        id=f"{KIND}:{ref}",
        owner_uid=str(row.get("ownerUid") or ""),
        session_id=str(row.get("sessionId") or ""),
        risk="routine",
        title=str(row.get("title") or ""),
        review=str(row.get("review") or ""),
        digest=str(row.get("digest") or ""),
        expires_at=float(row.get("expiresAt") or 0),
        state=state,
    )


class SourceDeleteApprovals:
    kind = KIND

    def pending(self, uid: str, session_id: str | None) -> list[ApprovalRequest]:
        rows = query_documents(COLLECTION, filters=[("ownerUid", "==", uid), ("state", "==", "pending")], limit=50)
        found = [_request(str(r["__id"]), r) for r in rows]
        return [r for r in found if r.state == "pending" and session_id in (None, r.session_id)]

    def get(self, ref: str, uid: str) -> ApprovalRequest | None:
        row = get_document(COLLECTION, ref)
        if row is None or row.get("ownerUid") != uid:
            return None
        return _request(ref, row)

    async def decide(self, ref: str, digest: str, decision: str, approver: Approver) -> Receipt:
        row = await asyncio.to_thread(get_document, COLLECTION, ref)
        if row is None:
            return Receipt(state="failed", message="This request is gone.")
        if row.get("state") != "pending":
            return Receipt(state=str(row.get("state")), message=f"This request is already {row.get('state')}.")
        # One use: claim it, then read back (a second click racing this one steps aside).
        claim = uuid.uuid4().hex
        await asyncio.to_thread(update_document, COLLECTION, ref, {"state": "executing", "claim": claim})
        again = await asyncio.to_thread(get_document, COLLECTION, ref) or {}
        if again.get("claim") != claim:
            return Receipt(state="executing", message="This request is already being decided.")
        if decision != "approve":
            await asyncio.to_thread(update_document, COLLECTION, ref, {"state": "cancelled", "decidedAt": time.time()})
            return Receipt(state="cancelled", message="Nothing was deleted.")
        try:
            details = await _delete(row)
        except (WriteRefused, GitHubError) as exc:
            message = getattr(exc, "message", str(exc))
            await asyncio.to_thread(
                update_document, COLLECTION, ref, {"state": "failed", "error": message, "decidedAt": time.time()}
            )
            return Receipt(state="failed", message=f"Nothing was deleted: {message}")
        await asyncio.to_thread(
            update_document, COLLECTION, ref, {"state": "completed", "result": details, "decidedAt": time.time()}
        )
        n = len(details["files"])
        return Receipt(
            state="completed",
            message=f"Deleted {n} file{'s' if n != 1 else ''} in commit {details['commit'][:7]}.",
            details=details,
        )


async def _delete(row: dict[str, Any]) -> dict[str, Any]:
    """Carry out an approved request, re-checking everything first."""
    uid, project_id = str(row["ownerUid"]), str(row["projectId"])
    src = await asyncio.to_thread(store.get_source, str(row["sourceId"]))
    if src is None or src.get("uid") != uid or not src.get("writable"):
        raise WriteRefused("SOURCE_WRITE_NONE", "That folder is no longer writable.")
    owner = await asyncio.to_thread(_owner, uid)
    cred = _writer(src, owner)
    repo, branch = str(src["repo"]), str(src["ref"])
    if (repo, branch) != (row["repo"], row["branch"]):
        raise WriteRefused("SOURCE_WRITE_STALE", "The source now follows another repository or branch.")
    rels = list(row["paths"])
    files: dict[str, bytes | None] = {repo_path(src, rel): None for rel in rels}
    expected = {repo_path(src, rel): row["expected"][rel] for rel in rels}
    message = f"{row['message']}\n\n{_trailers(owner, project_id, str(row.get('sessionId') or ''))}".strip()
    token = await credentials.write_token(cred, repo)
    done = await git_write.commit_files(token, repo, branch, files, expected, message)
    log.info("project %s: deleted %d file(s) from %s as %s", project_id, len(rels), repo, done.commit[:8])
    manifest = await asyncio.to_thread(store.manifest, src["id"])
    dest = ProjectDestination(owner, project_id, folder_of(src))
    for rel in rels:
        doc_id = str((manifest.get(rel) or {}).get("docId") or "")
        if doc_id:
            await asyncio.to_thread(dest.remove, doc_id)
        await asyncio.to_thread(store.drop_file, src["id"], rel)
    fields: dict[str, Any] = {}
    if str(src.get("syncedCommit") or "") == done.parent:
        fields.update(syncedCommit=done.commit, syncedAt=store.now_iso())
    if fields:
        await asyncio.to_thread(store.update_source, src["id"], fields)
    return {"commit": done.commit, "commit_url": done.url, "files": [f"{folder_of(src)}/{rel}" for rel in rels]}


def register() -> None:
    from adk.approvals import register_provider

    register_provider(SourceDeleteApprovals())
