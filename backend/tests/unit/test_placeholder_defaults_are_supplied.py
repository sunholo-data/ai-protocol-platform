"""A placeholder default in serving code must be supplied by the deploy.

Sprint TEMPLATE-INVERT, post-M5. This exists because the identity scrub kept
producing the same failure and I kept finding it one instance at a time:

  AUTH_OPERATOR_DOMAINS   traced by hand in M3
  bq _DATA_PROJECT        traced by hand in M3
  tools.yaml              found by the MCP-registry gate, after deploy
  ENTSOE_PROJECT          found by grep, only because someone asked
                          "are we sure there are no regressions?"

The scrub rewrote values that were load-bearing in the deployed environment.
Each one individually looked cosmetic — a project id in a default, a service
name in a constant — and each one broke something real. A placeholder is
strictly WORSE than an empty default, because it looks configured: the failure
surfaces as "dataset not found" deep in a tool call instead of "this deployment
has not configured X".

So the rule is enforced rather than remembered:

    a placeholder value in serving code must either be passed by the
    deploy pipelines, or be provably optional (empty-safe).

Scripts, tests and fixtures are out of scope — they do not run in a served
request.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# The values the sanitizer's scrub produces. A literal from this set appearing
# in serving code means a real value was replaced by a stand-in.
PLACEHOLDERS = (
    "your-project-id",
    "your-project-id-test",
    "your-project-id-prod",
    "your-entsoe-project",
    "your-deploy-project-id",
    "your-maps-project",
    "sa-platform",
    "yourcompany.com",
    "yourcompany.test",
    "acme-energy.example",
    "your-service-url.example",
)

# Directories whose code runs while serving a request.
SERVING_ROOTS = ("backend", "cli/aiplatform", "frontend/src")

EXCLUDE_PARTS = ("tests", "__tests__", "scripts", ".venv", "node_modules")

PIPELINES = ("cloudbuild.yaml", "backend/cloudbuild.yaml")


def _serving_files() -> list[Path]:
    out: list[Path] = []
    for root in SERVING_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for ext in ("*.py", "*.ts", "*.tsx"):
            out.extend(p for p in base.rglob(ext) if not any(part in EXCLUDE_PARTS for part in p.parts))
    return sorted(out)


def _pipeline_text() -> str:
    return "\n".join((REPO_ROOT / f).read_text(encoding="utf-8") for f in PIPELINES if (REPO_ROOT / f).is_file())


def test_serving_code_declares_no_placeholder_env_default() -> None:
    """`os.environ.get("X", "<placeholder>")` — the exact ENTSOE_PROJECT bug.

    An unconfigured deployment must fall back to something that FAILS
    RECOGNISABLY (empty), not to a string shaped like a real resource.
    """
    pattern = re.compile(
        r"""environ(?:\.get)?\(\s*["'](?P<var>[A-Z0-9_]+)["']\s*,\s*["'](?P<default>[^"']+)["']""",
    )
    offenders = []
    for path in _serving_files():
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for m in pattern.finditer(text):
            if m.group("default") in PLACEHOLDERS:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {m.group('var')}={m.group('default')!r}")

    assert offenders == [], (
        "serving code falls back to a placeholder that looks like a real "
        "resource. Use an empty default so the failure names itself, and pass "
        f"the real value from the deploy: {offenders}"
    )


@pytest.mark.parametrize("env_var", ["ENTSOE_PROJECT", "AUTH_OPERATOR_DOMAINS"])
def test_deployment_identity_env_vars_are_passed_by_the_pipelines(env_var: str) -> None:
    """Removing a default only works if the deploy supplies the value.

    That is the half that is easy to forget: M3 emptied AUTH_OPERATOR_DOMAINS
    and wired it the same day; M4 emptied nothing but rewrote ENTSOE_PROJECT's
    default and wired nothing, so the tool pointed at a project that does not
    exist.
    """
    text = _pipeline_text()
    assert text, "no pipelines found — did they move?"

    assert f"{env_var}=" in text, (
        f"{env_var} has no hardcoded default any more, so a pipeline MUST pass "
        "it or the deployed service runs unconfigured"
    )


def test_the_real_toolbox_config_reaches_the_build() -> None:
    """The deployed Toolbox must serve THIS deployment's toolset.

    M3 gitignored tools.yaml and generated it from the example — right for a
    fork, but it meant Cloud Build's clone had no real config and the deployed
    sidecar served the generic `example` toolset, silently stripping the
    customer's skills of their tools.

    Skips where the real config is absent, which is the correct state for the
    template and for any fork.
    """
    real = REPO_ROOT / "docs" / "customers" / "one" / "mcp-toolbox-tools.yaml"
    if not real.is_file():
        pytest.skip("no deployment-specific Toolbox config in this checkout")

    materialize = (REPO_ROOT / "scripts" / "materialize-config.sh").read_text()
    assert "docs/customers/one/mcp-toolbox-tools.yaml" in materialize, (
        "the real Toolbox config exists but materialize-config.sh does not "
        "prefer it, so the build would ship the generic example toolset"
    )

    root_pipeline = (REPO_ROOT / "cloudbuild.yaml").read_text()
    assert "materialize-config.sh" in root_pipeline, (
        "the toolbox image is built from a gitignored tools.yaml — the pipeline "
        "must materialize it before `docker build`, or COPY tools.yaml fails"
    )
