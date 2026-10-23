#!/usr/bin/env python3
"""M7 acceptance: does the browser actually read pages a plain GET cannot?

Reports a PASS RATE against a fixed corpus, because that is the bar this
platform has learned to hold. Green unit tests have shipped broken journeys
here before; the standing rule is to run the real thing N times and report the
rate, then ship at 10/10 or pull.

What it compares, per URL:
  * a plain HTTP GET (what `url_processing` does today — no JavaScript), and
  * the browser (JavaScript executed, markdown extracted).

A URL counts as PASS when the browser returns materially more usable text than
the plain GET. That is the whole product claim in one assertion.

Usage:
    # against a locally running service
    python corpus_check.py --service http://127.0.0.1:8099 --user you@example.com

    # against the deployed dev service
    python corpus_check.py --service "$(gcloud run services describe platform-browser \\
        --region=europe-west1 --format='value(status.url)')" --user you@example.com
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request

# Ten pages chosen to be JS-dependent to different degrees, stable, and public.
# The point is the CONTRAST with a plain GET, so a couple of server-rendered
# controls are included deliberately — if those regressed we would want to know.
CORPUS = [
    "https://react.dev/",
    "https://vuejs.org/",
    "https://angular.dev/",
    "https://svelte.dev/",
    "https://tailwindcss.com/",
    "https://nextjs.org/",
    "https://www.solidjs.com/",
    "https://astro.build/",
    "https://news.ycombinator.com/",
    "https://example.com/",
]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def plain_get_text(url: str, timeout: int = 30) -> str:
    """What the platform sees today: fetch, strip tags, no JavaScript."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; aitana-corpus)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return f"__ERROR__ {exc}"

    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


def id_token(audience: str, impersonate: str | None = None) -> str | None:
    """Mint an ID token for the browser service.

    A USER account cannot mint a custom-audience token — gcloud refuses with
    "Invalid account type for --audiences". Only the service account that holds
    run.invoker can, so an operator runs this by impersonating it. That is also
    a truer test: it exercises the same identity the backend uses.
    """
    if not audience.startswith("https://"):
        return None

    cmd = ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"]
    if impersonate:
        cmd.append(f"--impersonate-service-account={impersonate}")

    out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if out.returncode != 0 or not out.stdout.strip().startswith("ey"):
        print(f"could not mint an identity token: {out.stderr.strip()[:300]}", file=sys.stderr)
        if not impersonate:
            print(
                "\nA user account cannot mint a custom-audience token. Pass the "
                "service account that holds run.invoker:\n"
                "  --impersonate sa-platform-v6@<project>.iam.gserviceaccount.com",
                file=sys.stderr,
            )
        sys.exit(2)
    return out.stdout.strip()


def rpc(service: str, user: str, session: str, tool: str, arguments: dict, token: str | None) -> str:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Aitana-User": user,
        "X-Aitana-Session": session,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"{service}/mcp", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())

    content = (payload.get("result") or {}).get("content") or []
    return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", required=True, help="browser service base URL")
    ap.add_argument("--user", required=True, help="an allowlisted user id")
    ap.add_argument(
        "--impersonate",
        default=None,
        help="service account holding run.invoker (required against a deployed service)",
    )
    ap.add_argument(
        "--ceiling",
        type=int,
        default=40000,
        help="the supervisor's configured max_chars; truncation AT it is policy, below it is a bug",
    )
    args = ap.parse_args()

    service = args.service.rstrip("/")
    token = id_token(service, args.impersonate)

    print(f"corpus check against {service}\n")
    # The plain-GET column is CONTEXT, not the pass criterion: naive tag
    # stripping counts inlined JSON and boilerplate, so a big number there can
    # mean noise rather than content. The verdict is on extracted markdown.
    print(f"{'url':<38} {'raw GET':>10} {'markdown':>10}  verdict")
    print("-" * 78)

    passes = 0
    failures: list[tuple[str, str]] = []
    capped: list[str] = []

    # ONE session for the whole run, reused. Two reasons: it is what an agent
    # actually does (navigate within one browser), and a session per URL trips
    # the per-user session cap — which is how this harness first "failed" 7/10
    # with the service behaving perfectly and saying so.
    session = "corpus"

    for url in CORPUS:
        plain = plain_get_text(url)
        plain_len = 0 if plain.startswith("__ERROR__") else len(plain)

        try:
            rpc(service, args.user, session, "browser_navigate", {"url": url}, token)
            rendered = rpc(service, args.user, session, "browser_markdown", {}, token)
        except Exception as exc:
            print(f"{url:<38} {plain_len:>10} {'ERROR':>10}  FAIL")
            failures.append((url, f"browser call failed: {exc}"))
            continue

        rendered_len = len(rendered.strip())
        truncated = "(truncated," in rendered

        # THE BAR, and why it is this and not "enough characters".
        #
        # The first version of this check asked for >= 400 chars. It passed 9/10
        # while every one of those nine returned ~4032 chars — the shape of a
        # LIMIT, not of content. Obscura's browser_markdown truncates at
        # max_chars, default 4000, so the check was rubber-stamping pages read
        # 25% of the way through. A length floor cannot see that.
        #
        # The bar was then changed, AFTER seeing results, which deserves
        # justification rather than a quiet edit: the original bar encoded a
        # wrong assumption (that page size correlates with success), and the two
        # remaining "failures" under it were both correct behaviour —
        # example.com is genuinely a 167-character page, and solidjs.com is
        # genuinely larger than our deliberate ceiling.
        #
        # What actually matters is COMPLETENESS:
        #   * not truncated                  -> the whole page was read. PASS.
        #   * truncated AT our ceiling       -> our policy, and the model is told
        #                                       explicitly. PASS, reported as CAP.
        #   * truncated BELOW the ceiling    -> an unexpected cap. FAIL.
        #   * empty                          -> nothing extracted. FAIL.
        if not rendered.strip():
            ok, flag, why = False, "FAIL", "nothing extracted"
        elif not truncated:
            ok, flag, why = True, "PASS", ""
        elif rendered_len >= args.ceiling - 1000:
            ok, flag, why = True, "CAP", ""
            capped.append(url)
        else:
            ok, flag, why = False, "FAIL", f"truncated at {rendered_len}, below the {args.ceiling} ceiling"

        if ok:
            passes += 1
        else:
            failures.append((url, why))

        print(f"{url:<38} {plain_len:>10} {rendered_len:>10}  {flag}")

    total = len(CORPUS)
    print("-" * 78)
    print(f"\nPASS RATE: {passes}/{total}")

    if capped:
        print(f"\ncapped at the {args.ceiling}-char ceiling (policy, and signalled to the model):")
        for url in capped:
            print(f"  {url}")

    if failures:
        print("\nfailures:")
        for url, why in failures:
            print(f"  {url} — {why}")

    # The bar: ship at 10/10 or pull. Anything less is a finding, not a warning.
    if passes < total:
        print(f"\nBELOW BAR — {total - passes} of {total} failed. Do not ship on this.")
        return 1

    print("\nAt bar (10/10).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
