"""The GitHub workbench tab: what the agent looked at on GitHub, drawn from the
tool results and never by the model (Model B, tool-results-as-a2ui).

Turned on per MCP server with ``workbench: "github"`` on its ``mcp_servers`` row
(GitHub's hosted MCP server; docs/integrations/github-mcp.md). Every tool of that
server is offered here. The ones this module knows (commits, pull requests,
issues, branches, tags, releases, files, code and repository search) draw a
view; the rest draw nothing.

ONE TAB, UPDATED IN PLACE
The surface id is fixed, like the Browser tab, so a GitHub conversation keeps a
single tab rather than one per call. The latest view fills it. A pull request or
issue BUILDS UP: its checks, reviews, files and comments arrive in separate calls
and are kept beside the summary while the agent stays on the same one. A short
"Earlier in this chat" list links what was looked at before.

RENDERED BEFORE THE OFFLOAD
Registered with ``offload=True``: the transform sees the full response, and a
large one (a long PR list, a release with long notes) is still offloaded for the
model afterwards. Exempting these tools instead would put every large GitHub
result in the model's context.

The components are the Basic catalog's (`Text` renders markdown: links open in a
new tab, and inline code is monospace), so there is no GitHub-specific React.
Everything taken from GitHub is escaped before it is put into markdown, and only
http(s) links are made.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from adk.a2ui_result_render import BASIC_CATALOG_ID, WORKSPACE_SURFACE_ID, register

logger = logging.getLogger(__name__)

GITHUB_SURFACE = "github"
KIND = "github"
CONFIG_KEY = "temp:a2ui_mcp_render"
TOOL_ARGS_KEY = "temp:_a2ui_tool_args"
TOOL_NAME_KEY = "temp:_a2ui_tool_name"

GITHUB_TOOLS = [
    "list_commits",
    "search_commits",
    "get_commit",
    "list_pull_requests",
    "search_pull_requests",
    "pull_request_read",
    "list_issues",
    "search_issues",
    "issue_read",
    "list_branches",
    "list_tags",
    "list_releases",
    "get_latest_release",
    "get_release_by_tag",
    "get_file_contents",
    "search_code",
    "search_repositories",
]

_STATE_VIEW = "github_pane_view"
_STATE_HISTORY = "github_pane_history"

_MAX_ROWS = 30
_MAX_FILES = 40
_MAX_HISTORY = 10
_MAX_BODY = 700
_MAX_FILE_LINES = 60
_MAX_PASSED_LISTED = 8

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


# --- small helpers -----------------------------------------------------------


def _esc(value: Any) -> str:
    """Plain text safe to place inside markdown: no links, emphasis or HTML."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(value or "").split()))


def _url(value: Any) -> str:
    text = str(value or "")
    return text if text.startswith(("https://", "http://")) and " " not in text and ")" not in text else ""


def _link(label: str, url: Any) -> str:
    """``label`` is already escaped markdown."""
    href = _url(url)
    return f"[{label}]({href})" if href else label


def _code(value: Any) -> str:
    text = " ".join(str(value or "").split()).replace("`", "'")
    return f"`{text}`" if text else ""


def _when(value: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return f"{moment.day} {moment:%b %Y, %H:%M} UTC"


def _login(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("login") or user.get("name") or "")
    return str(user or "")


def _count(n: Any, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}" if isinstance(n, int) else ""


def _first_line(message: Any) -> str:
    return (str(message or "").strip().splitlines() or [""])[0]


def _join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


def _repo_url(repo: str) -> str:
    return f"https://github.com/{repo}" if re.fullmatch(r"[\w.-]+/[\w.-]+", repo or "") else ""


def _body_excerpt(body: Any) -> str:
    """A PR/issue/release body, kept readable and small: images become their alt
    text, HTML comments go, headings become bold lines."""
    text = str(body or "").replace("\r\n", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*(.+?)[ \t#]*$", r"**\1**", text, flags=re.MULTILINE)
    text = text.strip()
    if len(text) > _MAX_BODY:
        text = text[:_MAX_BODY].rsplit(" ", 1)[0] + " …"
    return text


# --- reading the MCP result --------------------------------------------------


def _blocks(typed_result: Any) -> list[dict]:
    if isinstance(typed_result, dict) and isinstance(typed_result.get("content"), list):
        return [b for b in typed_result["content"] if isinstance(b, dict)]
    return []


def _payload(typed_result: Any) -> Any:
    """The JSON the GitHub server put in the first text block, or None."""
    for block in _blocks(typed_result):
        if block.get("type") == "text":
            try:
                return json.loads(block.get("text") or "")
            except (TypeError, ValueError):
                return None
    return None


def _items(payload: Any, *keys: str) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _state(tool_context: Any) -> Any:
    return getattr(tool_context, "state", None) if tool_context is not None else None


def _get(state: Any, key: str, default: Any = None) -> Any:
    if state is None:
        return default
    try:
        value = state.get(key, default)
    except Exception:  # pragma: no cover - defensive
        return default
    return default if value is None else value


def _put(state: Any, key: str, value: Any) -> None:
    if state is None:
        return
    try:
        state[key] = value
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("github pane: could not record %s (%s)", key, exc)


# --- views: each returns {"key", "title", "url", "subtitle", "rows" | sections} ---
#
# A view is plain JSON (it is kept in session state between calls): strings and
# lists of strings, already markdown.


def _repo_of(args: dict) -> str:
    owner, repo = str(args.get("owner") or ""), str(args.get("repo") or "")
    return f"{owner}/{repo}" if owner and repo else ""


def _commit_row(item: dict, repo: str = "") -> str:
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    message = commit.get("message") or item.get("message")
    author = commit.get("author") if isinstance(commit.get("author"), dict) else item.get("author")
    who = _login(item.get("author")) or _login(author)
    date = (author or {}).get("date") if isinstance(author, dict) else ""
    found_in = item.get("repository")
    where = "" if repo else str(found_in.get("full_name") or "") if isinstance(found_in, dict) else ""
    sha = str(item.get("sha") or "")[:7]
    head = f"{_link(_code(sha), item.get('html_url'))} {_esc(_first_line(message))}"
    return f"{head}  \n{_join(_esc(who), _when(date), _code(where) if where else '')}"


def _commits_view(name: str, payload: Any, args: dict) -> dict | None:
    items = _items(payload, "items")
    repo = _repo_of(args)
    if name == "search_commits":
        title, subtitle = "Commits found", _code(args.get("query"))
    else:
        title = "Commits"
        subtitle = _join(
            _link(_code(repo), _repo_url(repo)) if repo else "",
            _code(args.get("sha")) if args.get("sha") else "",
            _code(args.get("path")) if args.get("path") else "",
            f"since {_when(args.get('since'))}" if args.get("since") else "",
        )
    rows = [_commit_row(i, repo) for i in items[:_MAX_ROWS]]
    return {
        "key": f"{name}:{repo}:{args.get('sha', '')}:{args.get('query', '')}",
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "empty": "No commits.",
        "label": f"Commits in {repo}" if repo else "Commit search",
        "url": _repo_url(repo) + "/commits" if repo else "",
    }


def _commit_view(payload: Any, args: dict) -> dict | None:
    if not isinstance(payload, dict) or not payload.get("sha"):
        return None
    repo = _repo_of(args)
    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    message = str(commit.get("message") or "")
    sha = str(payload["sha"])[:7]
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    lines = [
        _join(_esc(_login(payload.get("author")) or author.get("name")), _when(author.get("date"))),
        f"+{stats.get('additions', 0)} -{stats.get('deletions', 0)}" if stats else "",
    ]
    rest = "\n".join(message.splitlines()[1:]).strip()
    files = [
        f"{_code(f.get('filename'))} {_esc(f.get('status'))} +{f.get('additions', 0)} -{f.get('deletions', 0)}"
        for f in _items(payload.get("files"))[:_MAX_FILES]
    ]
    sections = []
    if rest:
        sections.append({"heading": "Message", "rows": [_esc(rest[:_MAX_BODY])]})
    sections.append({"heading": f"Files changed ({len(_items(payload.get('files')))})", "rows": files})
    return {
        "key": f"commit:{repo}:{payload['sha']}",
        "title": f"{_link(_code(sha), payload.get('html_url'))} {_esc(_first_line(message))}",
        "subtitle": _join(_link(_code(repo), _repo_url(repo)) if repo else "", *[x for x in lines if x]),
        "sections": sections,
        "label": f"Commit {sha} in {repo}" if repo else f"Commit {sha}",
        "url": _url(payload.get("html_url")),
    }


def _pr_state(item: dict) -> str:
    pr = item.get("pull_request") if isinstance(item.get("pull_request"), dict) else {}
    if item.get("draft"):
        return "Draft"
    if item.get("merged") or item.get("merged_at") or pr.get("merged_at"):
        return "Merged"
    return "Closed" if str(item.get("state", "")).lower() == "closed" else "Open"


def _issue_state(item: dict) -> str:
    state = str(item.get("state", "")).lower()
    reason = str(item.get("state_reason") or "").lower()
    if state == "closed":
        return "Closed as not planned" if reason == "not_planned" else "Closed"
    return "Open"


def _repo_from_url(url: Any) -> str:
    match = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/", str(url or ""))
    return match.group(1) if match else ""


def _pr_row(item: dict, *, issue: bool = False, show_repo: bool = False, repo: str = "") -> str:
    state = _issue_state(item) if issue else _pr_state(item)
    number = item.get("number")
    # list_issues returns no html_url; the repository the agent asked about gives it.
    url = item.get("html_url") or (f"{_repo_url(repo)}/{'issues' if issue else 'pull'}/{number}" if repo else "")
    head = f"{_link(f'#{number}', url)} **{_esc(item.get('title'))}**"
    repo = _repo_from_url(item.get("html_url")) if show_repo else ""
    return f"{head}  \n{_join(state, _code(repo) if repo else '', _esc(_login(item.get('user'))), _when(item.get('updated_at')))}"


def _list_view(name: str, payload: Any, args: dict) -> dict | None:
    issue = name in ("list_issues", "search_issues")
    items = _items(payload, "items", "issues")
    repo = _repo_of(args)
    searching = name.startswith("search_")
    noun = "Issues" if issue else "Pull requests"
    state = str(args.get("state") or "").lower()
    subtitle = (
        _code(args.get("query"))
        if searching
        else _join(_link(_code(repo), _repo_url(repo)) if repo else "", state.capitalize() if state else "")
    )
    total = (payload.get("total_count") or payload.get("totalCount")) if isinstance(payload, dict) else None
    if isinstance(total, int) and total > len(items):
        subtitle = _join(subtitle, f"showing {len(items)} of {total}")
    return {
        "key": f"{name}:{repo}:{state}:{args.get('query', '')}",
        "title": f"{noun} found" if searching else noun,
        "subtitle": subtitle,
        "rows": [_pr_row(i, issue=issue, show_repo=searching, repo=repo) for i in items[:_MAX_ROWS]],
        "empty": f"No {noun.lower()}.",
        "label": f"{noun} in {repo}" if repo and not searching else f"{noun} search",
        "url": (_repo_url(repo) + ("/issues" if issue else "/pulls")) if repo else "",
    }


_CHECK_WORDS = {
    "success": "✓ Passed",
    "failure": "✗ Failed",
    "timed_out": "✗ Timed out",
    "cancelled": "✗ Cancelled",
    "action_required": "! Needs action",
    "neutral": "○ Neutral",
    "skipped": "○ Skipped",
    "stale": "○ Stale",
}

_REVIEW_WORDS = {
    "APPROVED": "Approved",
    "CHANGES_REQUESTED": "Changes requested",
    "COMMENTED": "Commented",
    "DISMISSED": "Dismissed",
    "PENDING": "Pending",
}


def _check_rows(payload: Any) -> list[str]:
    """A summary line, then what needs attention first: failures, then running,
    then passed. Skipped and neutral checks are only counted: a release
    pipeline skips a dozen jobs on every PR, and listing them buries the result."""
    runs = _items(payload, "check_runs")
    failed, running, passed, quiet = [], [], [], []
    for run in runs:
        link = _link(_esc(run.get("name")), run.get("html_url"))
        conclusion = str(run.get("conclusion") or "")
        if run.get("status") != "completed":
            running.append(f"{'● Running' if run.get('status') == 'in_progress' else '● Queued'} — {link}")
        elif conclusion == "success":
            passed.append(f"✓ Passed — {link}")
        elif conclusion in ("neutral", "skipped", "stale"):
            quiet.append(conclusion)
        else:
            failed.append(f"{_CHECK_WORDS.get(conclusion, '✗ ' + _esc(conclusion))} — {link}")
    if not runs:
        return []
    counts = _join(
        f"{len(failed)} failed" if failed else "",
        f"{len(running)} running" if running else "",
        f"{len(passed)} passed" if passed else "",
        f"{len(quiet)} skipped" if quiet else "",
    )
    verdict = "✗ Failing" if failed else "● In progress" if running else "✓ All passed"
    # The passes are worth listing only when there are few; otherwise the
    # summary says it, and the rows would bury a failure.
    shown = failed + running + (passed if len(passed) <= _MAX_PASSED_LISTED else [])
    return [f"**{verdict}** · {counts}", *shown[:_MAX_ROWS]]


def _status_rows(payload: Any) -> list[str]:
    return [
        f"{_esc(s.get('state')).capitalize()} — {_link(_esc(s.get('context')), s.get('target_url'))}"
        for s in _items(payload, "statuses")[:_MAX_ROWS]
    ]


def _comment_rows(payload: Any) -> list[str]:
    rows = []
    for c in _items(payload, "comments")[:_MAX_ROWS]:
        text = " ".join(str(c.get("body") or "").split())
        text = text[:280] + (" …" if len(text) > 280 else "")
        where = f" on {_code(c.get('path'))}" if c.get("path") else ""
        rows.append(
            f"**{_esc(_login(c.get('user')))}**{where} · {_link(_when(c.get('created_at')) or 'comment', c.get('html_url'))}  \n{_esc(text)}"
        )
    return rows


_PR_SECTIONS = {
    "get_check_runs": "Checks",
    "get_status": "Commit statuses",
    "get_reviews": "Reviews",
    "get_files": "Files changed",
    "get_commits": "Commits",
    "get_comments": "Comments",
    "get_review_comments": "Review comments",
}


def _pr_section(method: str, payload: Any) -> list[str] | None:
    if method == "get_check_runs":
        return _check_rows(payload)
    if method == "get_status":
        return _status_rows(payload)
    if method == "get_reviews":
        return [
            f"{_REVIEW_WORDS.get(str(r.get('state')), _esc(r.get('state')))} — **{_esc(_login(r.get('user')))}**"
            + (f" · {_link(_when(r.get('submitted_at')), r.get('html_url'))}" if r.get("submitted_at") else "")
            for r in _items(payload)[:_MAX_ROWS]
        ]
    if method == "get_files":
        return [
            f"{_code(f.get('filename'))} {_esc(f.get('status'))} +{f.get('additions', 0)} -{f.get('deletions', 0)}"
            for f in _items(payload)[:_MAX_FILES]
        ]
    if method == "get_commits":
        return [_commit_row(c) for c in _items(payload)[:_MAX_ROWS]]
    if method in ("get_comments", "get_review_comments"):
        return _comment_rows(payload)
    return None


def _thread_view(name: str, payload: Any, args: dict, previous: dict | None) -> dict | None:
    """A pull request or issue; its sections build up across calls."""
    is_pr = name == "pull_request_read"
    repo = _repo_of(args)
    number = args.get("pullNumber") if is_pr else args.get("issue_number")
    method = str(args.get("method") or "get")
    key = f"{'pr' if is_pr else 'issue'}:{repo}#{number}"
    view = dict(previous) if previous and previous.get("key") == key else {}
    sections: dict[str, list[str]] = dict(view.get("section_map") or {})
    noun = "Pull request" if is_pr else "Issue"
    url = view.get("url") or (f"{_repo_url(repo)}/{'pull' if is_pr else 'issues'}/{number}" if repo else "")

    if method == "get":
        if not isinstance(payload, dict) or not payload.get("number"):
            return None
        url = _url(payload.get("html_url")) or url
        state = _pr_state(payload) if is_pr else _issue_state(payload)
        facts = [state, _esc(_login(payload.get("user")))]
        if is_pr:
            head = (payload.get("head") or {}).get("ref") if isinstance(payload.get("head"), dict) else ""
            base = (payload.get("base") or {}).get("ref") if isinstance(payload.get("base"), dict) else ""
            if head and base:
                facts.append(f"{_code(head)} → {_code(base)}")
            when = payload.get("merged_at") or payload.get("closed_at") or payload.get("updated_at")
            facts.append(_when(when))
            size = _join(
                f"+{payload.get('additions', 0)} -{payload.get('deletions', 0)}" if "additions" in payload else "",
                _count(payload.get("changed_files"), "file"),
                _count(payload.get("commits"), "commit"),
            )
        else:
            facts.append(_when(payload.get("updated_at")))
            labels = [_code(lbl.get("name")) for lbl in _items(payload.get("labels"))]
            size = " ".join(labels)
        view.update(
            title=f"{_link(f'#{number}', url)} {_esc(payload.get('title'))}",
            subtitle=_join(_link(_code(repo), _repo_url(repo)) if repo else "", *[f for f in facts if f]),
            detail=size,
            body=_body_excerpt(payload.get("body")),
        )
    else:
        rows = _pr_section(method, payload) if is_pr else _comment_rows(payload) if method == "get_comments" else None
        if rows is None:
            return None
        heading = _PR_SECTIONS.get(method, "Comments") if is_pr else "Comments"
        sections[heading] = rows or ["None."]
        view.setdefault("title", f"{_link(f'#{number}', url)} {noun}")
        view.setdefault("subtitle", _link(_code(repo), _repo_url(repo)) if repo else "")

    view.update(
        key=key,
        url=url,
        section_map=sections,
        label=f"{noun} #{number} in {repo}" if repo else f"{noun} #{number}",
    )
    return view


def _branches_view(name: str, payload: Any, args: dict) -> dict | None:
    repo = _repo_of(args)
    items = _items(payload)
    if name == "list_branches":
        rows = [
            _join(
                _link(
                    _code(b.get("name")), f"{_repo_url(repo)}/tree/{quote(str(b.get('name') or ''))}" if repo else ""
                ),
                "protected" if b.get("protected") else "",
            )
            for b in items[:_MAX_ROWS]
        ]
        title, empty = "Branches", "No branches."
    else:
        rows = [
            _join(
                _link(
                    _code(t.get("name")),
                    f"{_repo_url(repo)}/releases/tag/{quote(str(t.get('name') or ''))}" if repo else "",
                ),
                _code(str((t.get("commit") or {}).get("sha", ""))[:7]) if isinstance(t.get("commit"), dict) else "",
            )
            for t in items[:_MAX_ROWS]
        ]
        title, empty = "Tags", "No tags."
    return {
        "key": f"{name}:{repo}",
        "title": title,
        "subtitle": _link(_code(repo), _repo_url(repo)) if repo else "",
        "rows": rows,
        "empty": empty,
        "label": f"{title} in {repo}" if repo else title,
        "url": f"{_repo_url(repo)}/{'branches' if name == 'list_branches' else 'tags'}" if repo else "",
    }


def _size(n: Any) -> str:
    n = n if isinstance(n, int) else 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{max(1, round(n / 1000))} KB" if n else ""


def _release_row(r: dict) -> str:
    kind = "Pre-release" if r.get("prerelease") else "Draft" if r.get("draft") else ""
    return f"{_link('**' + _esc(r.get('name') or r.get('tag_name')) + '**', r.get('html_url'))}  \n{_join(_code(r.get('tag_name')), kind, _when(r.get('published_at') or r.get('created_at')))}"


def _releases_view(name: str, payload: Any, args: dict) -> dict | None:
    repo = _repo_of(args)
    if name == "list_releases":
        return {
            "key": f"{name}:{repo}",
            "title": "Releases",
            "subtitle": _link(_code(repo), _repo_url(repo)) if repo else "",
            "rows": [_release_row(r) for r in _items(payload)[:_MAX_ROWS]],
            "empty": "No releases.",
            "label": f"Releases in {repo}" if repo else "Releases",
            "url": f"{_repo_url(repo)}/releases" if repo else "",
        }
    if not isinstance(payload, dict) or not payload.get("tag_name"):
        return None
    assets = [
        _join(
            _link(_code(a.get("name")), a.get("browser_download_url")),
            _size(a.get("size")),
        )
        for a in _items(payload.get("assets"))[:_MAX_ROWS]
    ]
    sections = [{"heading": f"Assets ({len(_items(payload.get('assets')))})", "rows": assets}] if assets else []
    return {
        "key": f"release:{repo}:{payload.get('tag_name')}",
        "title": _link(_esc(payload.get("name") or payload.get("tag_name")), payload.get("html_url")),
        "subtitle": _join(
            _link(_code(repo), _repo_url(repo)) if repo else "",
            _code(payload.get("tag_name")),
            "Pre-release" if payload.get("prerelease") else "",
            _when(payload.get("published_at") or payload.get("created_at")),
        ),
        "body": _body_excerpt(payload.get("body")),
        "body_heading": "Release notes",
        "sections": sections,
        "label": f"Release {payload.get('tag_name')} of {repo}" if repo else f"Release {payload.get('tag_name')}",
        "url": _url(payload.get("html_url")),
    }


_FENCE_LANG = {
    "py": "python",
    "ts": "ts",
    "tsx": "tsx",
    "js": "js",
    "jsx": "jsx",
    "json": "json",
    "md": "markdown",
    "yaml": "yaml",
    "yml": "yaml",
    "sh": "bash",
    "go": "go",
    "sql": "sql",
    "toml": "toml",
    "html": "html",
    "css": "css",
    "rs": "rust",
    "java": "java",
}


def _file_view(typed_result: Any, payload: Any, args: dict) -> dict | None:
    repo = _repo_of(args)
    path = str(args.get("path") or "").strip("/")
    ref = str(args.get("ref") or args.get("sha") or "")
    tree_url = f"{_repo_url(repo)}/tree/{quote(ref or 'HEAD')}/{quote(path)}" if repo else ""
    if isinstance(payload, list):  # a directory
        entries = sorted(_items(payload), key=lambda e: (e.get("type") != "dir", str(e.get("name"))))
        rows = [
            _link(_code(str(e.get("name")) + ("/" if e.get("type") == "dir" else "")), e.get("html_url"))
            for e in entries[: _MAX_ROWS * 2]
        ]
        return {
            "key": f"dir:{repo}:{ref}:{path}",
            "title": _link(_code(path or "/"), tree_url),
            "subtitle": _join(_link(_code(repo), _repo_url(repo)) if repo else "", _code(ref) if ref else ""),
            "rows": rows,
            "empty": "Empty folder.",
            "label": f"{path or '/'} in {repo}",
            "url": tree_url,
        }
    text = None
    for block in _blocks(typed_result):
        resource = block.get("resource") if block.get("type") == "resource" else None
        if isinstance(resource, dict) and isinstance(resource.get("text"), str):
            text = resource["text"]
            break
    if text is None:
        return None
    lines = text.splitlines()
    shown = "\n".join(lines[:_MAX_FILE_LINES]).replace("```", "'''")
    lang = _FENCE_LANG.get(path.rsplit(".", 1)[-1].lower(), "") if "." in path else ""
    blob_url = f"{_repo_url(repo)}/blob/{quote(ref or 'HEAD')}/{quote(path)}" if repo else ""
    more = f"Showing the first {_MAX_FILE_LINES} of {len(lines)} lines." if len(lines) > _MAX_FILE_LINES else ""
    return {
        "key": f"file:{repo}:{ref}:{path}",
        "title": _link(_code(path), blob_url),
        "subtitle": _join(
            _link(_code(repo), _repo_url(repo)) if repo else "", _code(ref) if ref else "", f"{len(lines)} lines", more
        ),
        "code": f"```{lang}\n{shown}\n```",
        "label": f"{path} in {repo}",
        "url": blob_url,
    }


def _search_view(name: str, payload: Any, args: dict) -> dict | None:
    items = _items(payload, "items")
    if name == "search_code":
        rows = [
            _join(
                _link(
                    _code(i.get("path")),
                    i.get("html_url")
                    or (
                        f"{_repo_url(str(i.get('repository') or ''))}/blob/HEAD/{quote(str(i.get('path') or ''))}"
                        if isinstance(i.get("repository"), str)
                        else ""
                    ),
                ),
                _code(i.get("repository"))
                if isinstance(i.get("repository"), str)
                else _code((i.get("repository") or {}).get("full_name")),
            )
            for i in items[:_MAX_ROWS]
        ]
        title, empty = "Code found", "No matches."
    else:
        rows = [
            f"{_link('**' + _esc(r.get('full_name')) + '**', r.get('html_url'))}  \n"
            + _join(
                _esc(r.get("description"))[:160],
                _esc(r.get("language")),
                "archived" if r.get("archived") else "",
                f"updated {_when(r.get('updated_at'))}" if r.get("updated_at") else "",
            )
            for r in items[:_MAX_ROWS]
        ]
        title, empty = "Repositories", "No repositories."
    total = payload.get("total_count") if isinstance(payload, dict) else None
    subtitle = _code(args.get("query"))
    if isinstance(total, int) and total > len(items):
        subtitle = _join(subtitle, f"showing {len(items)} of {total}")
    return {
        "key": f"{name}:{args.get('query', '')}",
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "empty": empty,
        "label": f"{title}: {args.get('query', '')}"[:80],
        "url": "",
    }


def _view(name: str, typed_result: Any, args: dict, previous: dict | None) -> dict | None:
    payload = _payload(typed_result)
    if name in ("list_commits", "search_commits"):
        return _commits_view(name, payload, args)
    if name == "get_commit":
        return _commit_view(payload, args)
    if name in ("list_pull_requests", "search_pull_requests", "list_issues", "search_issues"):
        return _list_view(name, payload, args)
    if name in ("pull_request_read", "issue_read"):
        return _thread_view(name, payload, args, previous)
    if name in ("list_branches", "list_tags"):
        return _branches_view(name, payload, args)
    if name in ("list_releases", "get_latest_release", "get_release_by_tag"):
        return _releases_view(name, payload, args)
    if name == "get_file_contents":
        return _file_view(typed_result, payload, args)
    if name in ("search_code", "search_repositories"):
        return _search_view(name, payload, args)
    return None


# --- drawing -----------------------------------------------------------------


class _Tree:
    """Flattened A2UI v0.9 components (same shape as the browser pane's)."""

    def __init__(self) -> None:
        self.components: list[dict[str, Any]] = []
        self._seq = 0

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def text(self, text: str, variant: str | None = None) -> str:
        # Never "caption": the SDK renders <caption>, which is only valid in a table.
        comp: dict[str, Any] = {"id": self._id("t"), "component": "Text", "text": text}
        if variant:
            comp["variant"] = variant
        self.components.append(comp)
        return comp["id"]

    def column(self, children: list[str]) -> str:
        cid = self._id("col")
        self.components.append({"id": cid, "component": "Column", "children": children})
        return cid

    def list_(self, children: list[str]) -> str:
        cid = self._id("list")
        self.components.append({"id": cid, "component": "List", "children": children})
        return cid

    def card(self, child: str) -> str:
        cid = self._id("card")
        self.components.append({"id": cid, "component": "Card", "child": child})
        return cid

    def root(self, children: list[str]) -> None:
        self.components.append({"id": "root", "component": "Column", "children": children})

    def messages(self) -> list[dict[str, Any]]:
        return [
            {"version": "v0.9", "createSurface": {"surfaceId": WORKSPACE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
            {"version": "v0.9", "updateComponents": {"surfaceId": WORKSPACE_SURFACE_ID, "components": self.components}},
        ]


def _draw(view: dict, history: list[dict]) -> list[dict[str, Any]]:
    """Every Text is the `body` variant: the workbench renders only that one as
    full markdown (links, code, headings). Headings are markdown `###`/`####`."""
    tree = _Tree()
    children: list[str] = []

    def heading(text: str, level: int = 3) -> str:
        return tree.text(f"{'#' * level} {text}", "body")

    # h4 for the title, h3 for section labels: on the workbench h3 is the quiet,
    # upper-case label style ("Pages visited" on the Browser tab).
    header = [heading(view.get("title") or "GitHub", 4)]
    if view.get("subtitle"):
        header.append(tree.text(view["subtitle"], "body"))
    if view.get("detail"):
        header.append(tree.text(view["detail"], "body"))
    children.append(tree.card(tree.column(header)))

    if view.get("code"):
        children.append(tree.card(tree.text(view["code"], "body")))

    if "rows" in view:
        rows = view.get("rows") or []
        children.append(
            tree.list_([tree.text(r, "body") for r in rows])
            if rows
            else tree.text(view.get("empty") or "Nothing found.", "body")
        )

    # What was fetched about it (checks, reviews, files, assets) comes before
    # the description: it is usually what the question was about.
    sections = list(view.get("sections") or [])
    sections += [{"heading": h, "rows": r} for h, r in (view.get("section_map") or {}).items()]
    for section in sections:
        rows = section.get("rows") or []
        body = tree.list_([tree.text(r, "body") for r in rows]) if rows else tree.text("None.", "body")
        children.append(tree.card(tree.column([heading(_esc(section.get("heading"))), body])))

    if view.get("body"):
        children.append(
            tree.card(
                tree.column([heading(view.get("body_heading") or "Description"), tree.text(view["body"], "body")])
            )
        )

    earlier = [h for h in history if h.get("key") != view.get("key")][-_MAX_HISTORY:]
    if earlier:
        children.append(heading("Earlier in this chat"))
        children.append(
            tree.list_([tree.text(_link(_esc(h.get("label")), h.get("url")), "body") for h in reversed(earlier)])
        )

    tree.root(children)
    return tree.messages()


def github_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Draw the GitHub tab for this result, keeping the pull request or issue
    being looked at and a short history in session state."""
    if isinstance(typed_result, dict) and (typed_result.get("isError") or typed_result.get("error")):
        return None  # the model says what went wrong; an error tab would repeat it
    state = _state(tool_context)
    name = str(_get(state, TOOL_NAME_KEY, "") or "")
    args = _get(state, TOOL_ARGS_KEY, {}) or {}
    previous = _get(state, _STATE_VIEW)
    view = _view(
        name, typed_result, args if isinstance(args, dict) else {}, previous if isinstance(previous, dict) else None
    )
    if view is None:
        return None

    history = [h for h in (_get(state, _STATE_HISTORY, []) or []) if isinstance(h, dict)]
    entry = {"key": view.get("key", ""), "label": view.get("label", ""), "url": view.get("url", "")}
    history = [h for h in history if h.get("key") != entry["key"]] + [entry]
    history = history[-(_MAX_HISTORY + 1) :]
    _put(state, _STATE_VIEW, view)
    _put(state, _STATE_HISTORY, history)
    return _draw(view, history)


def _is_github(tool_context: Any) -> bool:
    config = _get(_state(tool_context), CONFIG_KEY) or {}
    return isinstance(config, dict) and config.get("kind") == KIND


def _artifact_meta(typed_result: Any) -> dict[str, str]:
    return {"kind": "github", "title": "GitHub", "description": "What was looked at on GitHub in this chat"}


register(
    github_to_a2ui,
    tool_names=GITHUB_TOOLS,
    name="github_activity",
    surface=GITHUB_SURFACE,
    artifact_meta=_artifact_meta,
    context_matcher=_is_github,
    offload=True,
)

__all__ = ["GITHUB_SURFACE", "GITHUB_TOOLS", "github_to_a2ui"]
