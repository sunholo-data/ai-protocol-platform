"""``POST /api/webhooks/github`` — sync on push, for deployments GitHub can reach
(github-sources.md §7, P5).

Optional and additive: the scheduler stays the backstop, so a lost delivery only
means the change arrives on the next tick instead of now. Off unless
``GITHUB_WEBHOOK_SECRET`` is set (404 then); every delivery's
``X-Hub-Signature-256`` is checked against it before the body is read as JSON.

A push syncs the project sources that follow that repository and branch and whose
path the push touched, within GitHub's 10-second delivery window. Whatever doesn't
finish in that window is ``partial``, and the scheduler finishes it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sources import store
from sources.scheduler import sync_if_due

log = logging.getLogger(__name__)

router = APIRouter(tags=["github-webhook"])

# GitHub gives a delivery 10 s; answer well inside it.
_BUDGET_SECONDS = 7.0


def _verified_body(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _touched_paths(payload: dict[str, Any]) -> set[str] | None:
    """Every path the push changed, or None when the payload doesn't list them
    (GitHub caps a push's commit list): then every source on the branch counts."""
    commits = payload.get("commits")
    if not isinstance(commits, list) or not commits or len(commits) >= 20:
        return None
    paths: set[str] = set()
    for commit in commits:
        for key in ("added", "modified", "removed"):
            paths.update(str(p) for p in commit.get(key) or [])
    return paths


def _affects(src: dict[str, Any], touched: set[str] | None) -> bool:
    path = str(src.get("path") or "")
    if touched is None or not path:
        return True
    return any(p == path or p.startswith(f"{path}/") for p in touched)


@router.post("/api/webhooks/github")
async def github_push(request: Request) -> dict[str, Any]:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.body()
    if not _verified_body(secret, body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=401, detail="Bad signature")
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"ok": True}
    if event != "push":
        return {"ignored": event}
    try:
        payload = json.loads(body)
        repo = str(payload["repository"]["full_name"]).lower()
        branch = str(payload["ref"]).removeprefix("refs/heads/")
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Not a push payload") from exc

    touched = _touched_paths(payload)
    matching = [
        s
        for s in store.all_sources()
        if s.get("type") == "github"
        and str(s.get("repo") or "").lower() == repo
        and s.get("ref") == branch
        and _affects(s, touched)
    ]
    deadline = time.monotonic() + _BUDGET_SECONDS
    results: list[dict[str, Any]] = []
    for src in matching:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            results.append({"id": src["id"], "outcome": "deferred: the scheduler takes it"})
            continue
        try:
            results.append(await sync_if_due(src, budget_seconds=remaining))
        except Exception as exc:  # one source never stops the others
            log.exception("source %s: push sync failed", src.get("id"))
            results.append({"id": src["id"], "outcome": f"error: {exc}"[:300]})
    log.info("github push %s@%s: %d source(s) %s", repo, branch, len(matching), json.dumps(results))
    return {"sources": len(matching), "results": results}
