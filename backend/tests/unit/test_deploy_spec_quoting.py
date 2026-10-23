"""scripts/deploy-run-service.sh — shell quoting, which changed meaning on extraction.

v6.44.0 moved the service spec out of cloudbuild.yaml's inline script and into a
real shell file so both pipelines could share one definition. One thing silently
changed meaning in the move.

In the YAML, Cloud Build expanded `${_FOO}` TEXTUALLY before any shell ran, so
`'...${_FOO}...'` in single quotes was harmless — the substitution had already
happened. In a shell script the SHELL does the expanding, and single quotes
prevent it: gcloud receives the literal string `${_ADMIN_SEED_ALLOWED_SAS}`.

That does not fail the deploy. It succeeds, with a wrong value, which is the
expensive kind. On 2026-09-04 it took a dev deploy: `seed-platform-skills`
returned 403 because the allowlist was the literal text, and AUTH_OPERATOR_DOMAINS
was literal too — on an env with AUTH_REQUIRE_KNOWN_DOMAIN=1 that locks out every
operator, and nothing in the build output says so.

The guard is mechanical because the eye is bad at it: a single quote and a double
quote differ by one pixel and the failure appears an env away.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_SCRIPT = REPO_ROOT / "scripts" / "deploy-run-service.sh"

# A gcloud FLAG whose value is single-quoted and contains a ${...} reference —
# `--set-env-vars='...${_FOO}...'`. Scoped to flags on purpose: a single quote
# inside a double-quoted string (`echo "service '${_X}' does not exist"`) expands
# perfectly well, and a guard that flags those gets muted rather than fixed.
_SINGLE_QUOTED_FLAG_WITH_VAR = re.compile(r"--[a-z-]+='[^'\n]*\$\{[^'\n]*'")


def _code_lines() -> list[tuple[int, str]]:
    lines = []
    for n, raw in enumerate(SPEC_SCRIPT.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append((n, raw))
    return lines


def test_script_exists():
    assert SPEC_SCRIPT.is_file(), f"{SPEC_SCRIPT} is missing"


def test_no_substitution_is_ever_single_quoted():
    offenders = [(n, line.strip()) for n, line in _code_lines() if _SINGLE_QUOTED_FLAG_WITH_VAR.search(line)]
    assert not offenders, (
        "single quotes stop the shell expanding a substitution, so gcloud gets the "
        "literal '${_FOO}' and the deploy SUCCEEDS with a wrong value:\n"
        + "\n".join(f"  line {n}: {text}" for n, text in offenders)
        + "\nUse double quotes. See this file's docstring for what it cost."
    )


@pytest.mark.parametrize(
    "var",
    [
        "_ADMIN_SEED_ALLOWED_SAS",
        "_AUTH_OPERATOR_DOMAINS",
        "_VISUAL_EVIDENCE_ALLOWED_UIDS",
        "_VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS",
        "_VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS",
        "_VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS",
    ],
)
def test_the_comma_bearing_values_keep_their_custom_delimiter(var: str):
    """Both values contain commas, which gcloud otherwise reads as a pair separator.

    `^|^` tells it to split on | instead. Dropping the prefix truncates the value
    at the first comma — a subtler version of the same silent-wrong-value failure:
    an operator-domain allowlist that keeps only its first domain still deploys.
    """
    name = var.lstrip("_")
    text = SPEC_SCRIPT.read_text(encoding="utf-8")
    assert f'--set-env-vars="^|^{name}=${{{var}}}"' in text, (
        f'{name} must be passed as --set-env-vars="^|^{name}=${{{var}}}" — '
        "double-quoted so the shell expands it, and ^|^-prefixed so gcloud does "
        "not split the value on its own commas."
    )


VISUAL_DEFAULTS = {
    "_VISUAL_EVIDENCE_ENABLED": "false",
    "_VISUAL_EVIDENCE_ALLOWED_UIDS": "",
    "_VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS": "",
    "_VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS": "",
    "_VISUAL_EVIDENCE_MODEL": "",
    "_VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS": "",
}


def test_visual_gates_are_default_off_and_reach_both_backend_specs():
    """A browser flag alone must not leave the authenticated route unwired."""
    branch = yaml.safe_load((REPO_ROOT / "cloudbuild.yaml").read_text())
    standalone = yaml.safe_load((REPO_ROOT / "backend/cloudbuild.yaml").read_text())
    promotion = yaml.safe_load((REPO_ROOT / "cloudbuild.promote.yaml").read_text())
    for config in (branch, standalone, promotion):
        assert {key: config["substitutions"].get(key) for key in VISUAL_DEFAULTS} == VISUAL_DEFAULTS
    branch_deploy = next(step for step in branch["steps"] if step.get("id") == "deploy")
    script = SPEC_SCRIPT.read_text()
    standalone_flags = "\n".join(arg for step in standalone["steps"] for arg in step.get("args", []))
    for key in VISUAL_DEFAULTS:
        assert f"{key}=${{{key}}}" in branch_deploy["env"]
        assert f"{key.lstrip('_')}=${{{key}}}" in script
        assert f"{key.lstrip('_')}=${{{key}}}" in standalone_flags
    resolve = next(step for step in promotion["steps"] if step.get("id") == "resolve-spec-config")
    default_json = re.search(r"<<'JSON'\n(.*?)\nJSON", resolve["args"][-1], re.DOTALL)
    assert default_json
    spec_defaults = json.loads(default_json.group(1))
    assert {key: spec_defaults.get(key) for key in VISUAL_DEFAULTS} == {key: f"${{{key}}}" for key in VISUAL_DEFAULTS}
    frontend = next(step for step in promotion["steps"] if step.get("id") == "build-frontend")
    assert "resolve-spec-config" in frontend["waitFor"]
    assert ". /workspace/spec_env" in frontend["args"][-1]
    assert 'NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED="$${_VISUAL_EVIDENCE_ENABLED}"' in frontend["args"][-1]
