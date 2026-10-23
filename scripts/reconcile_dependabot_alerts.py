"""Reconcile alerts proven fixed by every locked copy and advisory range.

Uses stdlib only. Unknown ranges and prereleases stay open for manual review.
Absence from npm audit is never used as evidence of a fix.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    "frontend/package-lock.json",
    "infrastructure/mcp-sandbox/package-lock.json",
)


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Unsupported version: {value!r}")
    return tuple(map(int, value.split(".")))


def in_range(version, expression):
    """Understand GitHub comparator ranges; fail closed on other syntax."""
    actual = stable_version(version)
    branches = []
    for branch in expression.split("||"):
        terms = []
        for term in branch.split(","):
            match = re.fullmatch(r"\s*(>=|<=|>|<|=)?\s*(\d+\.\d+\.\d+)\s*", term)
            if not match:
                raise ValueError(f"Unsupported advisory range: {expression!r}")
            op, raw = match.groups()
            expected = stable_version(raw)
            terms.append(
                {
                    ">=": actual >= expected,
                    "<=": actual <= expected,
                    ">": actual > expected,
                    "<": actual < expected,
                    "=": actual == expected,
                    None: actual == expected,
                }[op]
            )
        branches.append(all(terms))
    return any(branches)


def fixed_in_lock(alert, lock):
    package = alert["dependency"]["package"]["name"]
    packages = lock["packages"]
    if (
        not isinstance(packages, dict)
        or "" not in packages
        or not lock.get("lockfileVersion")
    ):
        raise ValueError("Invalid npm lockfile")
    copies = [
        entry["version"]
        for path, entry in packages.items()
        if path.endswith("node_modules/" + package)
    ]
    if not copies:
        return True
    # Check ALL ranges for this package, including parallel release branches.
    ranges = [
        v["vulnerable_version_range"]
        for v in alert["security_advisory"]["vulnerabilities"]
        if v["package"]["ecosystem"] == "npm" and v["package"]["name"] == package
    ]
    if not ranges:
        raise ValueError("Missing advisory ranges")
    return not any(
        in_range(version, expression) for version in copies for expression in ranges
    )


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True, cwd=ROOT)


def decode_pages(raw):
    # gh --paginate emits one JSON document per page. Do not combine --slurp
    # with --jq: supported flag combinations differ between CLI releases.
    decoder = json.JSONDecoder()
    pages = []
    while raw.strip():
        raw = raw.lstrip()
        page, end = decoder.raw_decode(raw)
        if not isinstance(page, list):
            raise ValueError("Expected an alert array from GitHub")
        pages.append(page)
        raw = raw[end:]
    if not pages:
        raise ValueError("GitHub returned no alert inventory")
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = (
        os.environ.get("GITHUB_REPOSITORY")
        or gh(
            "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
        ).strip()
    )
    locks = {path: json.loads((ROOT / path).read_text()) for path in MANIFESTS}
    if args.apply:
        # Never dismiss alerts based on uncommitted or unpushed fixes.
        subprocess.run(
            ["git", "diff", "--exit-code", "HEAD", "--", *MANIFESTS],
            cwd=ROOT,
            check=True,
        )
        default = gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()
        remote_head = gh(
            "api", f"repos/{repo}/commits/{default}", "--jq", ".sha"
        ).strip()
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        if head != remote_head:
            raise RuntimeError(
                "Refusing dismissal: checkout is not remote default-branch HEAD"
            )
    pages = decode_pages(
        gh(
            "api",
            f"repos/{repo}/dependabot/alerts",
            "--paginate",
            "--jq",
            '[.[] | select(.state == "open")]',
        )
    )
    fixed = kept = skipped = 0
    for alert in (alert for page in pages for alert in page):
        if alert["state"] != "open":
            continue
        number = alert["number"]
        path = alert["dependency"]["manifest_path"]
        if alert["dependency"]["package"]["ecosystem"] != "npm" or path not in locks:
            print(f"SKIP #{number}: unsupported manifest/ecosystem; manual review")
            skipped += 1
            continue
        try:
            is_fixed = fixed_in_lock(alert, locks[path])
        except (ValueError, KeyError, TypeError) as exc:
            print(f"KEEP #{number}: cannot prove fixed: {exc}")
            kept += 1
            continue
        if not is_fixed:
            print(f"KEEP #{number}: a locked version remains affected")
            kept += 1
            continue
        print(
            f"FIXED #{number}: package removed or every locked version outside all advisory ranges"
        )
        if args.apply:
            gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{repo}/dependabot/alerts/{number}",
                "-f",
                "state=dismissed",
                "-f",
                "dismissed_reason=fix_started",
                "-f",
                f"dismissed_comment=Verified on {head}: every copy in {path} is removed or outside all advisory ranges.",
                "--silent",
            )
        fixed += 1
    print(
        f"== Summary ==\n  {'dismissed' if args.apply else 'would-dismiss'}: {fixed}\n"
        f"  kept: {kept}\n  manual review: {skipped}"
    )


if __name__ == "__main__":
    main()
