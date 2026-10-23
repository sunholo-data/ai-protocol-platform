#!/usr/bin/env python3
"""Fail on single-dollar variable references inside Cloud Build step scripts.

Cloud Build resolves ``$NAME`` in a step's args as a SUBSTITUTION before bash
ever sees the script. If ``NAME`` is not a declared substitution or a built-in,
the whole build dies at submit time with:

    invalid value for 'build.substitutions': key in the template "NAME" is not
    a valid built-in substitution

Shell variables must therefore be written with a doubled dollar (``$$NAME``),
which Cloud Build unescapes to a single one for bash.

WHY THIS EXISTS: on 2026-08-04 a COMMENT added inside the promote pipeline's
build-frontend script mentioned the shell variable it was documenting, with one
dollar. Cloud Build does not care that it is a comment — it never gets to bash.
The failure only appears when that pipeline is actually submitted, and the
promote pipeline runs rarely (it is the ONLY path to prod), so a bug planted
here can sit unnoticed until the moment someone needs to ship to prod. The dev
and test builds were green throughout.

Usage:  python3 scripts/check-cloudbuild-substitutions.py [file ...]
        (defaults to the repo's Cloud Build configs)
Exit:   0 clean · 1 offending reference found · 2 bad usage
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

# https://cloud.google.com/build/docs/configuring-builds/substitute-variable-values
BUILTINS = {
    "PROJECT_ID",
    "PROJECT_NUMBER",
    "BUILD_ID",
    "LOCATION",
    "TRIGGER_NAME",
    "TRIGGER_BUILD_CONFIG_PATH",
    "COMMIT_SHA",
    "REVISION_ID",
    "SHORT_SHA",
    "REPO_NAME",
    "REPO_FULL_NAME",
    "BRANCH_NAME",
    "TAG_NAME",
    "REF_NAME",
    "SERVICE_ACCOUNT",
    "SERVICE_ACCOUNT_EMAIL",
}

DEFAULT_FILES = [
    "cloudbuild.yaml",
    "cloudbuild.promote.yaml",
    "infrastructure/mcp-sandbox/cloudbuild.yaml",
    "infrastructure/env/cloudbuild.terraform.yaml",
]

# A single $ not preceded by another $, followed by a bare identifier.
# ${_FOO} is fine (substitution), $$FOO is fine (escaped shell var).
SINGLE_DOLLAR = re.compile(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)")


#: A gcloud custom-delimiter prefix (`^|^`, `^@^`, …) sitting in a BASH step.
#: Only meaningful on a flag value; only safe when the whole argument is quoted,
#: because the delimiter character is very often a shell metacharacter.
DELIM_FLAG = re.compile(r"(--[a-z-]+=)\^(.)\^")


def _shell_delimiter_problems(path: Path, step_id: str, script: str) -> list[str]:
    """Flag an UNQUOTED `^x^` custom delimiter inside a bash step.

    `cloudbuild.yaml` passes each gcloud flag as its own YAML list item, which
    never reaches a shell, so `--set-env-vars=^|^A=1|B=2` is fine there. A bash
    step is different: `|` is a pipe, `&` backgrounds, `;` separates. Unquoted,
    bash tears the flag apart and gcloud receives `--set-env-vars=^`, failing
    with "Bad syntax for dict arg: [^]".

    Cost of not having this: the first v0.1.33 prod promote (build 5055df14,
    2026-09-03) died at `deploy` — AFTER copying the backend image and pushing
    the frontend, so it failed halfway through a release rather than at submit.
    """
    problems: list[str] = []
    for raw_line in script.splitlines():
        match = DELIM_FLAG.search(raw_line)
        if not match:
            continue
        delimiter = match.group(2)
        if delimiter.isalnum():
            continue  # not a shell metacharacter; harmless bare
        # Quoted anywhere on the line is good enough — the flag is one argument.
        before = raw_line[: match.start()]
        if '"' in before or "'" in before:
            continue
        problems.append(
            f"{path}: step '{step_id}': unquoted custom delimiter '^{delimiter}^' in a bash step.\n"
            f"    Wrap the whole flag in double quotes: \"{match.group(1)}^{delimiter}^...\"\n"
            f"    {raw_line.strip()}"
        )
    return problems


def check(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not doc or "steps" not in doc:
        return []
    allowed = set(doc.get("substitutions") or {}) | BUILTINS
    problems: list[str] = []
    for index, step in enumerate(doc["steps"]):
        step_id = step.get("id", f"step[{index}]")
        is_shell_step = str(step.get("entrypoint", "")).endswith(("bash", "sh"))
        for arg in step.get("args") or []:
            if not isinstance(arg, str):
                continue
            if is_shell_step:
                problems.extend(_shell_delimiter_problems(path, step_id, arg))
            for match in SINGLE_DOLLAR.finditer(arg):
                name = match.group(1)
                if name in allowed:
                    continue
                line = arg[: match.start()].count("\n") + 1
                snippet = arg.splitlines()[line - 1].strip()
                problems.append(
                    f"{path}: step '{step_id}' (script line {line}): "
                    f"${name} -> write $${name} for a shell variable\n"
                    f"    {snippet}"
                )
    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path(f) for f in DEFAULT_FILES]
    problems: list[str] = []
    checked = 0
    for path in targets:
        if not path.exists():
            continue
        checked += 1
        problems.extend(check(path))

    if problems:
        print("Cloud Build: problem(s) found in step scripts.\n")
        print(
            "Two failure modes, both of which reach a HALF-FINISHED release:\n"
            "  $NAME   — Cloud Build resolves it as a SUBSTITUTION before bash runs,\n"
            "            so an unknown key fails at submit time, comments included.\n"
            "  ^x^     — an unquoted gcloud custom delimiter in a bash step; the\n"
            "            shell eats the metacharacter and gcloud sees a bare '^'.\n"
        )
        for p in problems:
            print(f"  {p}")
        print(f"\n{len(problems)} problem(s) across {checked} file(s).")
        return 1

    print(f"OK: no single-dollar shell references in {checked} Cloud Build config(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
