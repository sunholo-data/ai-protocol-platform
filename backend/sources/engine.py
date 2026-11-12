"""The sync engine: one source, one run (github-sources.md §5).

``plan`` is pure: given the files under the source's path and the source's
manifest, it decides what to add, update, remove and skip, and whether the
removal cap holds the removals back. ``run_sync`` resolves the branch, plans,
and applies the plan through a :class:`Destination` (the project library today,
a knowledge base next) within a time budget. A run that hits its budget stops
cleanly. The manifest records every finished file, so the next run carries on
where this one stopped.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol

from sources import store
from sources.github import GitHubClient, GitHubError, TreeEntry
from sources.unsearchable import unsearchable_reason

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 1000
# The removal cap (§5 step 7): a run never deletes more than this many, or more
# than this share of what the source owns, without confirmation.
REMOVAL_CAP_COUNT = 50
REMOVAL_CAP_SHARE = 0.25
DEFAULT_BUDGET_SECONDS = 240
PROGRESS_EVERY_S = 2.0
_CONCURRENCY = 4
# Lists in a stored report are capped; the counts are always complete.
_REPORT_LIST_MAX = 200


class Destination(Protocol):
    """Where a source's files become documents."""

    def accepts(self, path: str) -> str | None:
        """None when the file can be stored, else why it is skipped."""

    def exists(self, path: str) -> bool:
        """Whether an unowned document already sits where ``path`` would land."""

    async def put(self, path: str, data: bytes, source_ref: dict[str, Any]) -> PutResult:
        """Store ``data`` for ``path``. Never raises: failures are in the result."""

    def remove(self, doc_id: str) -> bool:
        """Delete a document the source owns. False when it was already gone."""


@dataclass
class PutResult:
    doc_id: str = ""
    outcome: str = "failed"  # added | updated | adopted | unchanged | moved | failed
    parse_status: str = ""
    error: str = ""


@dataclass
class Plan:
    add: list[TreeEntry] = field(default_factory=list)
    update: list[TreeEntry] = field(default_factory=list)
    remove: list[dict[str, Any]] = field(default_factory=list)
    # (manifest row of the old path, entry at the new path): the same bytes moved.
    move: list[tuple[dict[str, Any], TreeEntry]] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    removals_held: bool = False


def _matches(path: str, patterns: list[str]) -> bool:
    # fnmatch's * crosses "/", so "*.md" matches at any depth and "**" is "*".
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def skip_reason(entry: TreeEntry, include: list[str], exclude: list[str]) -> str | None:
    """Why the engine itself skips a file, before the destination is asked."""
    parts = PurePosixPath(entry.path).parts
    if any(part.startswith(".") for part in parts):
        return "hidden file or folder"
    if include and not _matches(entry.path, include):
        return "not in the include patterns"
    if exclude and _matches(entry.path, exclude):
        return "excluded by a pattern"
    if entry.size > MAX_FILE_BYTES:
        return f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
    return None


def plan(
    entries: list[TreeEntry],
    manifest: dict[str, dict[str, Any]],
    *,
    include: list[str],
    exclude: list[str],
    accepts: Any,
    confirm_removals: bool = False,
) -> Plan:
    """Decide the run. ``accepts`` is the destination's type check."""
    out = Plan()
    kept: dict[str, TreeEntry] = {}
    every_path = {e.path for e in entries}
    for entry in sorted(entries, key=lambda e: e.path):
        reason = (
            skip_reason(entry, include, exclude)
            or unsearchable_reason(entry.path, entry.size, every_path)
            or accepts(entry.path)
        )
        if reason:
            out.skipped.append({"path": entry.path, "reason": reason})
            continue
        kept[entry.path] = entry
    for path, entry in kept.items():
        row = manifest.get(path)
        if row is None:
            out.add.append(entry)
        elif row.get("blobSha") != entry.sha:
            out.update.append(entry)
        else:
            out.unchanged += 1
    # A file that is now skipped (excluded, too large) is removed like a deleted
    # one: the source no longer provides it.
    out.remove = [row for path, row in sorted(manifest.items()) if path not in kept]
    out.add, out.remove, out.move = _pair_moves(out.add, out.remove)
    owned = len(manifest)
    if out.remove and not confirm_removals:
        too_many = len(out.remove) > REMOVAL_CAP_COUNT or len(out.remove) > owned * REMOVAL_CAP_SHARE
        # A tiny source (say 2 of 3 files deleted) isn't a mistake worth a stop.
        out.removals_held = too_many and len(out.remove) > 3
    return out


def _pair_moves(
    add: list[TreeEntry], remove: list[dict[str, Any]]
) -> tuple[list[TreeEntry], list[dict[str, Any]], list[tuple[dict[str, Any], TreeEntry]]]:
    """A file that left one path while the same bytes (blob sha) arrived at
    another, with the same type, was moved or renamed. Its document follows it,
    keeping its id (so chats stay linked) and its parsed text (no re-parse).
    One old path pairs with one new path; copies beyond that are plain adds."""
    leaving: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in remove:
        if row.get("docId") and row.get("blobSha"):
            key = (str(row["blobSha"]), PurePosixPath(str(row["path"])).suffix.lower())
            leaving.setdefault(key, []).append(row)
    moves: list[tuple[dict[str, Any], TreeEntry]] = []
    still_added: list[TreeEntry] = []
    for entry in add:
        rows = leaving.get((entry.sha, PurePosixPath(entry.path).suffix.lower()))
        if rows:
            moves.append((rows.pop(0), entry))
        else:
            still_added.append(entry)
    moved_from = {id(row) for row, _entry in moves}
    return still_added, [r for r in remove if id(r) not in moved_from], moves


def _capped(items: list[Any]) -> list[Any]:
    return items[:_REPORT_LIST_MAX]


def source_ref(source: dict[str, Any], commit: str, entry: TreeEntry) -> dict[str, Any]:
    full = "/".join(p for p in (source.get("path") or "", entry.path) if p)
    return {
        "sourceId": source["id"],
        "type": "github",
        "repo": source["repo"],
        "ref": source["ref"],
        "path": full,
        "blobSha": entry.sha,
        "commit": commit,
        "url": f"https://github.com/{source['repo']}/blob/{source['ref']}/{full}",
    }


async def run_sync(
    source: dict[str, Any],
    *,
    client: GitHubClient,
    destination: Destination,
    dry_run: bool = False,
    confirm_removals: bool = False,
    force: bool = False,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Run one sync and return its report. Writes the report onto the source
    unless ``dry_run``. Raises nothing for GitHub failures: they are the report."""
    started = time.monotonic()
    report: dict[str, Any] = {
        "dryRun": dry_run,
        "startedAt": store.now_iso(),
        "commit": "",
        "status": "ok",
        "counts": {
            "added": 0,
            "updated": 0,
            "adopted": 0,
            "moved": 0,
            "unchanged": 0,
            "removed": 0,
            "skipped": 0,
            "failed": 0,
            "remaining": 0,
        },
        "skipped": [],
        "failed": [],
        "removalsHeld": [],
    }
    run_id = ""
    if not dry_run:
        run_id = str(uuid.uuid4())
        if not store.acquire_lease(source["id"], run_id, seconds=int(budget_seconds) + 120):
            report.update(status="busy", message="Another sync of this source is running.")
            return report

    try:
        try:
            state = await client.resolve_ref(source["repo"], source["ref"])
            assert state is not None  # no etag was sent
            report["commit"] = state.commit
            unchanged_branch = state.commit == source.get("syncedCommit") and source.get("status") == "ok" and not force
            if unchanged_branch:
                report["status"] = "ok"
                report["message"] = "Nothing new since the last sync."
                return _finish(source, run_id, report, state.etag, started)
            entries = await client.tree(source["repo"], state.commit, source.get("path") or "")
        except GitHubError as exc:
            report.update(status="failed", error={"code": exc.code, "message": exc.message})
            return _finish(source, run_id, report, "", started)

        if len(entries) > MAX_FILES:
            report.update(
                status="failed",
                error={
                    "code": "SOURCE_TOO_LARGE",
                    "message": f"{len(entries)} files under this path; a source holds at most {MAX_FILES}.",
                },
            )
            return _finish(source, run_id, report, "", started)

        owned = store.manifest(source["id"])
        decided = plan(
            entries,
            owned,
            include=list(source.get("include") or []),
            exclude=list(source.get("exclude") or []),
            accepts=destination.accepts,
            confirm_removals=confirm_removals,
        )
        counts = report["counts"]
        counts["unchanged"] = decided.unchanged
        counts["skipped"] = len(decided.skipped)
        report["skipped"] = _capped(decided.skipped)
        report["parseFailed"] = _capped(
            [
                {"path": p, "error": r.get("parseError", "")}
                for p, r in sorted(owned.items())
                if r.get("parseStatus") == "failed"
            ]
        )

        if dry_run:
            adopt = [e for e in decided.add if destination.exists(e.path)]
            counts["adopted"] = len(adopt)
            counts["added"] = len(decided.add) - len(adopt)
            counts["updated"] = len(decided.update)
            counts["moved"] = len(decided.move)
            report["plan"] = {
                "move": _capped([f"{row['path']} → {entry.path}" for row, entry in decided.move]),
                "adopt": _capped([e.path for e in adopt]),
                "add": _capped([e.path for e in decided.add if e not in adopt]),
                "update": _capped([e.path for e in decided.update]),
                "remove": _capped([r["path"] for r in decided.remove]),
            }
            if decided.removals_held:
                report["removalsHeld"] = _capped([r["path"] for r in decided.remove])
            else:
                counts["removed"] = len(decided.remove)
            return _finish(source, run_id, report, state.etag, started)

        await _apply(source, state.commit, decided, client, destination, report, started, budget_seconds)
        return _finish(source, run_id, report, state.etag, started)
    except Exception as exc:  # never leave a source stuck in "running"
        log.exception("source %s: sync failed", source.get("id"))
        report.update(status="failed", error={"code": "SYNC_FAILED", "message": str(exc)[:300]})
        return _finish(source, run_id, report, "", started)


async def _apply(
    source: dict[str, Any],
    commit: str,
    decided: Plan,
    client: GitHubClient,
    destination: Destination,
    report: dict[str, Any],
    started: float,
    budget_seconds: float,
) -> None:
    counts = report["counts"]
    sem = asyncio.Semaphore(_CONCURRENCY)
    out_of_time = False
    # Moves first: no download, no parse — the document follows its file. A move
    # the destination can't make (a name already taken there) falls back to an
    # add at the new path and, once that is stored, removing the old document.
    fallback: list[dict[str, Any]] = []
    stored: set[str] = set()
    mover = getattr(destination, "move", None)
    for row, entry in decided.move:
        moved = None
        if mover is not None:
            try:
                moved = await asyncio.to_thread(mover, str(row["docId"]), entry.path, source_ref(source, commit, entry))
            except Exception as exc:
                log.warning("source %s: moving %s to %s failed: %s", source.get("id"), row["path"], entry.path, exc)
        if moved is not None and moved.outcome == "moved":
            store.drop_file(source["id"], str(row["path"]))
            store.put_file(
                source["id"],
                entry.path,
                blob_sha=entry.sha,
                doc_id=moved.doc_id,
                parse_status=str(row.get("parseStatus") or "parsed"),
                parse_error=str(row.get("parseError") or ""),
            )
            counts["moved"] += 1
        else:
            decided.add.append(entry)
            fallback.append({**row, "_to": entry.path})
    # Progress on the source while the run works, so the project page can show
    # "120 of 309" to anyone who opens it — including after leaving and coming
    # back. At most one write per PROGRESS_EVERY_S, plus the last.
    total = len(decided.add) + len(decided.update)
    done = 0
    last_write = 0.0

    def tick() -> None:
        nonlocal done, last_write
        done += 1
        if done == total or time.monotonic() - last_write >= PROGRESS_EVERY_S:
            last_write = time.monotonic()
            store.update_source(source["id"], {"progress": {"done": done, "total": total}})

    if total:
        store.update_source(source["id"], {"progress": {"done": 0, "total": total}})

    async def one(entry: TreeEntry, is_update: bool) -> None:
        nonlocal out_of_time
        async with sem:
            if time.monotonic() - started > budget_seconds:
                out_of_time = True
                counts["remaining"] += 1
                return
            try:
                await store_one(entry, is_update)
            finally:
                tick()

    async def store_one(entry: TreeEntry, is_update: bool) -> None:
        try:
            data = await client.blob(source["repo"], entry.sha)
        except GitHubError as exc:
            counts["failed"] += 1
            report["failed"].append({"path": entry.path, "error": exc.message})
            return
        result = await destination.put(entry.path, data, source_ref(source, commit, entry))
        if result.outcome == "failed":
            counts["failed"] += 1
            report["failed"].append({"path": entry.path, "error": result.error})
            return
        store.put_file(
            source["id"],
            entry.path,
            blob_sha=entry.sha,
            doc_id=result.doc_id,
            parse_status=result.parse_status,
            parse_error=result.error,
        )
        stored.add(entry.path)
        counts[_count_key(result.outcome, is_update)] += 1
        if result.parse_status == "failed":
            report["parseFailed"].append({"path": entry.path, "error": result.error})

    await asyncio.gather(
        *(one(e, False) for e in decided.add),
        *(one(e, True) for e in decided.update),
    )

    for row in fallback:  # a move made as add + remove: drop the old one once the new one is in
        if row["_to"] in stored:
            if row.get("docId"):
                destination.remove(str(row["docId"]))
            store.drop_file(source["id"], str(row["path"]))
            counts["removed"] += 1

    if decided.removals_held:
        report["removalsHeld"] = _capped([r["path"] for r in decided.remove])
    elif not out_of_time:
        for row in decided.remove:
            if row.get("docId"):
                destination.remove(str(row["docId"]))
            store.drop_file(source["id"], str(row["path"]))
            counts["removed"] += 1

    report["failed"] = _capped(report["failed"])
    report["parseFailed"] = _capped(report["parseFailed"])
    if decided.removals_held:
        report["status"] = "needs_review"
        report["message"] = (
            f"{len(decided.remove)} files would be removed. That is more than the safety limit, so nothing was "
            "removed. Check the list, then sync again and confirm the removals."
        )
    elif out_of_time:
        report["status"] = "partial"
        report["message"] = (
            f"Stopped at the time limit with {counts['remaining']} files to go. The next sync continues."
        )
    elif counts["failed"]:
        report["status"] = "partial"


def _count_key(outcome: str, is_update: bool) -> str:
    """Which report count a stored file goes to.

    A file new to the manifest that landed on an existing document was adopted
    (identical or refreshed); one the source already owned was updated, unless
    its bytes turned out identical."""
    if is_update:
        return "unchanged" if outcome == "unchanged" else "updated"
    return "added" if outcome == "added" else "adopted"


def _finish(source: dict[str, Any], run_id: str, report: dict[str, Any], etag: str, started: float) -> dict[str, Any]:
    report["finishedAt"] = store.now_iso()
    report["seconds"] = round(time.monotonic() - started, 1)
    if run_id:
        fields: dict[str, Any] = {
            "status": report["status"],
            "lastReport": report,
            "syncedAt": report["finishedAt"],
            "progress": None,
        }
        if report["status"] in ("ok", "partial", "needs_review") and report.get("commit"):
            fields["syncedCommit"] = report["commit"]
        if etag:
            fields["etag"] = etag
        store.release_lease(source["id"], run_id, fields)
    return report
