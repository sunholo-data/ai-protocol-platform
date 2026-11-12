"""Sync every due project source: the push trigger (github-sources.md §7).

Called every few minutes — by Cloud Scheduler on ``POST /internal/sources/sync-due``
(OIDC-gated like the Cloud Tasks routes), or on a host by
``python -m sources.scheduler`` from a timer. Either way it is the same function.

A source is due when its last run did not finish (``partial`` or ``failed``) or its
branch has moved. "Has it moved?" is one conditional request with the stored ETag,
which GitHub does not count against the rate limit, so an idle repo costs nothing.
A source nobody has synced yet (``new``) waits: its owner runs the first sync after
seeing the preview. A source whose project is gone is deleted.

One call shares one time budget across sources; whatever it doesn't reach, the next
call does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

import db.projects as projects_db
from auth import User
from auth.access_context import build_access_context
from internal_tasks.auth import verify_google_oidc
from sources import credentials, store
from sources.engine import DEFAULT_BUDGET_SECONDS, run_sync
from sources.folder_sync import sync_folder
from sources.github import GitHubClient, GitHubError
from sources.project_destination import ProjectDestination

log = logging.getLogger(__name__)

# Below this, starting another source would only stop it at once.
_MIN_RUN_SECONDS = 20


def _owner(src: dict[str, Any]) -> User | None:
    """The owner as they are NOW — uid, email, domain and group tags, from the same
    authority as a request — or None when they can't be resolved (removed, or the
    identity store is down): then nothing syncs on their behalf."""
    from auth.firebase_auth import resolve_user_by_uid

    return resolve_user_by_uid(str(src.get("uid") or ""))


def _refuse(src: dict[str, Any], code: str, message: str) -> None:
    """Show the owner why scheduled syncs stopped, on the project page."""
    report = {
        "status": "failed",
        "error": {"code": code, "message": message},
        "counts": {},
        "finishedAt": store.now_iso(),
    }
    store.update_source(src["id"], {"status": "failed", "lastReport": report})


async def sync_if_due(src: dict[str, Any], *, budget_seconds: float) -> dict[str, Any]:
    out: dict[str, Any] = {"id": src["id"], "projectId": src.get("projectId")}
    if projects_db.get_project(str(src.get("uid") or ""), str(src.get("projectId") or "")) is None:
        store.delete_source(src["id"])
        return {**out, "outcome": "removed: project deleted"}
    if src.get("status") in ("new", "running"):
        return {**out, "outcome": f"skipped: {src.get('status')}"}
    if src.get("type") == "folder":
        report = sync_folder(src)
        changed = report["counts"]["added"] or report["counts"]["removed"]
        return {**out, "outcome": report["status"] if changed or report["status"] != "ok" else "unchanged"}
    owner = _owner(src)
    if owner is None:
        return {**out, "outcome": "skipped: owner could not be resolved"}
    # The owner's access is checked again on every run (§16): a withdrawn tag or a
    # narrowed credential stops the sync, and the project page says why.
    cred = credentials.get(str(src.get("credentialRef") or ""))
    repo = str(src.get("repo") or "")
    if cred is None or not cred.usable_by(build_access_context(owner)) or not cred.covers(repo):
        message = f"The owner may no longer read {repo} through {src.get('credentialRef')!r}."
        if (src.get("lastReport") or {}).get("error", {}).get("code") != "SOURCE_CREDENTIAL_NOT_ALLOWED":
            _refuse(src, "SOURCE_CREDENTIAL_NOT_ALLOWED", message)
        return {**out, "outcome": "refused: credential not allowed"}
    try:
        client = GitHubClient(await credentials.token(cred, repo))
    except GitHubError as exc:
        return {**out, "outcome": f"error: {exc.message}"}
    if src.get("status") not in ("partial", "failed"):
        try:
            state = await client.resolve_ref(str(src["repo"]), str(src["ref"]), etag=str(src.get("etag") or ""))
        except GitHubError as exc:
            return {**out, "outcome": f"error: {exc.message}"}
        if state is None or state.commit == src.get("syncedCommit"):
            return {**out, "outcome": "unchanged"}
    report = await run_sync(
        src,
        client=client,
        destination=ProjectDestination(owner, str(src["projectId"]), str(src.get("folderPrefix") or "")),
        budget_seconds=budget_seconds,
    )
    return {**out, "outcome": report.get("status"), "commit": report.get("commit"), "counts": report.get("counts")}


async def sync_due(*, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict[str, Any]:
    deadline = time.monotonic() + budget_seconds
    results: list[dict[str, Any]] = []
    for src in store.all_sources():
        remaining = deadline - time.monotonic()
        if remaining < _MIN_RUN_SECONDS:
            results.append({"id": src["id"], "outcome": "deferred: out of time"})
            continue
        try:
            results.append(await sync_if_due(src, budget_seconds=remaining))
        except Exception as exc:  # one source never stops the others
            log.exception("source %s: scheduled sync failed", src.get("id"))
            results.append({"id": src["id"], "outcome": f"error: {exc}"[:300]})
    synced = [r for r in results if r["outcome"] not in ("unchanged",) and not r["outcome"].startswith("skipped")]
    if synced:
        log.info("sources: scheduled sync %s", json.dumps(synced))
    return {"checked": len(results), "results": results}


# --- Cloud Scheduler -------------------------------------------------------------

router = APIRouter(prefix="/internal/sources", tags=["internal-sources"])


def _scheduler_caller(request: Request) -> str:
    return verify_google_oidc(
        request,
        os.environ.get("SOURCES_SCHEDULER_OIDC_SA", "").strip(),
        os.environ.get("SOURCES_SCHEDULER_AUDIENCE", "").strip(),
    )


@router.post("/sync-due")
async def sync_due_route(caller: Annotated[str, Depends(_scheduler_caller)]) -> dict[str, Any]:
    return await sync_due()


# --- host timer ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sources.scheduler", description=__doc__.splitlines()[0])
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS, help="seconds to spend, in total")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = asyncio.run(sync_due(budget_seconds=args.budget))
    print(json.dumps(result, indent=2))
    return 1 if any(str(r["outcome"]).startswith("error") or r["outcome"] == "failed" for r in result["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
