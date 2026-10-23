"""`aiplatform guide` — the user guide (/help) and its platform-guide skill.

    aiplatform --env dev guide verify [--runs 3] [--door <front-door-skill>]

The design's self-verification (docs/design/private/user-guide-and-platform-
guide-skill.md): against the DEPLOYED env only, it proves the pages render,
the knowledge base holds exactly this release's sections, and the skill
answers the fixed question set (scripts/user-guide/questions.tsv) by linking
the right section — and asserts the failures that would otherwise look
healthy: an uncovered question answered anyway, an invented button label, a
skill reaching more people than its knowledge base.

Needs the repo checkout (it reads the bundle the release was built from) and
AIPLATFORM_ID_TOKEN for an identity that holds the guide tags.
"""

from __future__ import annotations

import json as _json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import click
import httpx

from aiplatform.http import AIPlatformClient, APIError, resolve_base_url

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUNDLE = _REPO_ROOT / "backend" / "bundled" / "user-guide"
_QUESTIONS = _REPO_ROOT / "scripts" / "user-guide" / "questions.tsv"
GUIDE_SKILL = "platform-guide"
GUIDE_KB = "platform-guide"
_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@click.group()
def guide() -> None:
    """The user guide (/help) and the platform-guide skill."""


def _bundle() -> dict:
    subprocess.run(["node", str(_REPO_ROOT / "scripts/user-guide/bundle.mjs")], check=True, capture_output=True)
    return _json.loads((_BUNDLE / "manifest.json").read_text())


def expected_sections(manifest: dict) -> int:
    """Indexed documents the release should hold: per page, its ## and ### sections plus an intro when it has one."""
    n = 0
    for page in manifest["pages"]:
        body = (_BUNDLE / f"{page['slug']}.md").read_text()
        heads = [s for s in page.get("sections") or [] if s.get("level") in (2, 3)]
        intro = body.split("\n## ", 1)[0] if not body.startswith("## ") else ""
        n += len(heads) + (1 if intro.strip() else 0)
    return n


def _norm(text: str) -> str:
    """Digits and bracketed placeholders become '#', so 'Searchable · 2', 'Searchable · [number]'
    and 'Uploads 2026-09-04' match the guide's own examples."""
    text = re.sub(r"\[[^\]]*\]|\d+", "#", text.replace("`", ""))
    return re.sub(r"\s+", " ", text).strip().lower()


def guide_labels(manifest: dict) -> str:
    """The guide's text, normalised: a bolded phrase in an answer is invented only if it appears
    nowhere in the guide (labels, code, prose)."""
    texts = ((_BUNDLE / f"{page['slug']}.md").read_text() for page in manifest["pages"])
    # Keep a link's text, drop its target, before brackets become placeholders.
    return " \n ".join(_norm(re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)) for t in texts)


def run_turn(env: str, skill_id: str, message: str, timeout: float = 240.0) -> dict:
    """One real chat turn (the same stream `skill chat` reads): reply text, tool calls, errors."""
    base_url = resolve_base_url(env)
    headers = AIPlatformClient(env=env, base_url=base_url)._auth_headers()  # noqa: SLF001
    headers["Accept"] = "text/event-stream"
    body = {
        "threadId": f"guide-verify-{uuid.uuid4().hex[:12]}",
        "runId": f"run-{uuid.uuid4().hex[:8]}",
        "messages": [{"id": f"msg-{uuid.uuid4().hex[:8]}", "role": "user", "content": message}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    text, tools, errors, names = [], [], [], {}
    with httpx.stream(
        "POST", f"{base_url}/api/skill/{skill_id}/stream", headers=headers, json=body, timeout=timeout
    ) as resp:
        if resp.status_code >= 400:
            raise APIError(f"stream {skill_id} returned {resp.status_code}: {resp.read().decode()[:300]}")
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                ev = _json.loads(line[5:].strip())
            except _json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "TEXT_MESSAGE_CONTENT":
                text.append(ev.get("delta") or "")
            elif kind == "TOOL_CALL_START":
                names[ev.get("toolCallId")] = ev.get("toolCallName")
            elif kind == "TOOL_CALL_ARGS":
                names[str(ev.get("toolCallId")) + ":a"] = names.get(str(ev.get("toolCallId")) + ":a", "") + (
                    ev.get("delta") or ""
                )
            elif kind == "TOOL_CALL_END":
                cid = ev.get("toolCallId")
                tools.append((names.get(cid, "?"), names.get(f"{cid}:a", "")))
            elif kind == "RUN_ERROR":
                errors.append(ev.get("message") or "RUN_ERROR")
    return {"reply": "".join(text).strip(), "tools": tools, "errors": errors}


def judge(question: str, expected: str, turn: dict, labels: str) -> str:
    """'' when the answer passes, else why it failed."""
    reply = turn["reply"]
    if turn["errors"]:
        return f"RUN_ERROR {turn['errors'][0][:120]}"
    if not any(name == "knowledge_search" for name, _ in turn["tools"]):
        return "never searched the guide"
    invented = [
        lbl
        for lbl in re.findall(r"\*\*([^*]+)\*\*", reply)
        if _norm(lbl) not in labels and not lbl.endswith((".", ":"))
    ]
    if expected == "-":
        if "/help" not in reply:
            return "uncovered question, but the answer doesn't point to /help"
        if invented:
            return f"uncovered question answered with invented labels {invented[:3]}"
        return ""
    # "a#x|b#y": either section answers the question (the guide covers some tasks twice).
    wanted = [f"/help/{alt}" for alt in expected.split("|")]
    if not any(w in reply for w in wanted):
        links = re.findall(r"/help/[\w-]+#[\w-]+", reply)
        return f"expected a link to {' or '.join(wanted)}, got {links or 'none'}"
    if invented:
        return f"labels not in the guide: {invented[:3]}"
    return ""


@guide.command("verify")
@click.option("--runs", default=1, show_default=True, help="Ask each question this many times (report a pass rate).")
@click.option(
    "--door", default="", help="Also ask the first questions through this front-door skill slug (it must hand off)."
)
@click.option("--only", default=0, help="Ask only the first N questions (0 = all).")
@click.pass_context
def verify(ctx: click.Context, runs: int, door: str, only: int) -> None:
    """Prove the deployed user guide works — pages, knowledge base, skill — and that its guards hold."""
    env = ctx.obj["env"]
    client = AIPlatformClient(env=env, base_url=resolve_base_url(env))
    manifest = _bundle()
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        click.echo(f"  {'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            failures.append(what)

    # 1. Pages: every section anchor of this release is on the deployed page.
    click.echo("pages")
    site = resolve_base_url(env).removesuffix("/api/proxy")
    for page in manifest["pages"]:
        url = f"{site}/help/{page['slug']}"
        r = httpx.get(url, timeout=60, follow_redirects=True)
        # The sign-in gate renders client-side, so the server HTML carries the page in
        # Next's data payload rather than as id="…" attributes: look for the anchors.
        missing = [s["anchor"] for s in page["sections"] if s["anchor"] not in r.text]
        check(
            r.status_code == 200 and not missing,
            f"/help/{page['slug']} renders all {len(page['sections'])} anchors"
            + (f" (missing {missing[:3]})" if missing else ""),
        )
    chrome = shutil.which("google-chrome") or (_CHROME if Path(_CHROME).exists() else "")
    if chrome:
        first = manifest["pages"][0]["slug"]
        dom = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--virtual-time-budget=10000",
                "--dump-dom",
                f"{site}/help/{first}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
        # The guard: signed out, the guide body is never RENDERED as an element (the
        # data payload may carry it; the content is public-template-safe by construction).
        rendered = 'data-testid="guide-body"' in dom
        seen = "sign-in panel" if "Sign-in required" in dom else ("loading" if "Loading…" in dom else "other")
        check(not rendered, f"signed out: the guide is not shown (saw: {seen})")
    else:
        click.echo("  SKIP  signed-out gate (no Chrome to render the client gate)")

    # 2. Knowledge base: this release's sections, with the skill's tags ⊆ the KB's.
    click.echo("knowledge base")
    want = expected_sections(manifest)
    try:
        st = client.get(f"/api/knowledge-bases/{GUIDE_KB}/status")
    except APIError as exc:
        st = {}
        check(False, f"{GUIDE_KB} status: {exc}")
    if st:
        state = (st.get("lastSyncSummary") or {}).get("state")
        check(state == "done", f"last sync finished (state={state})")
        check(st.get("documentCount") == want, f"{st.get('documentCount')} sections indexed, release has {want}")
    kbs = {k.get("kbId"): k for k in (client.get("/api/knowledge-bases") or [])}
    skills = {s.get("slug"): s for s in client.get("/api/skills?limit=200") or []}
    sk = skills.get(GUIDE_SKILL)
    check(sk is not None, f"{GUIDE_SKILL} skill is visible to this identity")
    kb_ac = (kbs.get(GUIDE_KB) or {}).get("accessControl") or {}
    if sk:
        sk_ac = sk.get("accessControl") or {}
        check(
            sk_ac.get("type") == "tagged" and kb_ac.get("type") == "tagged", "skill and KB are tag-gated (never public)"
        )
        check(
            set(sk_ac.get("tags") or []) <= set(kb_ac.get("tags") or []) and bool(sk_ac.get("tags")),
            f"skill tags {sk_ac.get('tags')} ⊆ KB tags {kb_ac.get('tags')}",
        )
    if failures or not sk:
        raise SystemExit(f"\n{len(failures)} check(s) failed before the skill was asked anything")

    # 3. The skill answers the fixed questions by linking the right section.
    labels = guide_labels(manifest)
    rows = [ln.split("\t", 1) for ln in _QUESTIONS.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    rows = rows[:only] if only else rows
    click.echo(f"skill ({len(rows)} questions × {runs})")
    passed = total = 0
    for expected, question in rows:
        for _ in range(runs):
            total += 1
            why = judge(question, expected, run_turn(env, sk["skillId"], question), labels)
            passed += not why
            click.echo(f"  {'PASS' if not why else 'FAIL'}  {question[:70]}" + (f"\n        → {why}" if why else ""))
    check(passed == total, f"pass rate {passed}/{total}")

    # 4. Their own setup (my_context): answers name what THEY have, and never invent.
    click.echo("your setup (my_context)")
    folder_names = [f.get("name") for f in (client.get("/api/folders") or {}).get("folders", []) if f.get("name")]
    turn = run_turn(env, sk["skillId"], "Which of my folders are searchable, and which aren't?")
    used = any(n == "my_context" for n, _ in turn["tools"])
    named = [n for n in folder_names if n in turn["reply"]]
    check(
        used and (named or not folder_names),
        f"folders question uses my_context and names your folders (named {named[:3]})",
    )
    turn = run_turn(env, sk["skillId"], "Which skill should I use to research a topic on the web? Give me the link.")
    links = set(re.findall(r"/chat/@[\w.-]+/[\w-]+", turn["reply"]))
    real = {f"/chat/@{s.get('ownerId')}/{s.get('slug')}" for s in skills.values()}
    invented = sorted(links - real)
    check(
        any(n == "my_context" for n, _ in turn["tools"]) and links and not invented,
        f"skill suggestion links only skills you can open (links {sorted(links)[:2]}, invented {invented})",
    )

    # 5. Through a front door: it hands off to the guide and the answer still cites.
    if door:
        click.echo(f"front door {door}")
        d = skills.get(door)
        check(d is not None, f"{door} is visible")
        for expected, question in [r for r in rows if r[0] != "-"][:3]:
            turn = run_turn(env, d["skillId"], question) if d else {"reply": "", "tools": [], "errors": ["missing"]}
            handed = any(n == "transfer_to_agent" and "platform_guide" in a.replace("-", "_") for n, a in turn["tools"])
            why = "" if handed else "did not hand off to platform-guide"
            why = why or judge(question, expected, turn, labels)
            check(not why, f"{question[:60]}" + (f" → {why}" if why else ""))

    if failures:
        raise SystemExit(f"\nFAILED: {len(failures)} check(s)")
    click.echo("\nguide verify: all checks passed")
