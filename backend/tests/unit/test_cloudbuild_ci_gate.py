"""The deploy pipeline must be gated on CI (v6.19.0, AIPLA #36).

The branch-push Cloud Build trigger and GitHub Actions CI are independent
systems — a push fires both in parallel with nothing linking them, so a commit
whose CI is red deploys anyway. A ruff-format failure reached dev on 2026-06-17
exactly this way.

These are STRUCTURAL assertions over cloudbuild.yaml rather than behavioural
tests: the failure mode is someone adding a new step (or a new pipeline) that
forgets to wait on the gate, and that is invisible until something broken
ships. Asserting the shape is what makes the gate un-bypassable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CLOUDBUILD = REPO_ROOT / "cloudbuild.yaml"

GATE_IDS = {"ci-gate-backend", "ci-gate-frontend"}


@pytest.fixture(scope="module")
def pipeline() -> dict:
    return yaml.safe_load(CLOUDBUILD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(pipeline: dict) -> list[dict]:
    return pipeline["steps"]


def test_both_gate_steps_exist(steps):
    ids = {s.get("id") for s in steps}
    assert GATE_IDS <= ids, f"missing CI gate step(s): {GATE_IDS - ids}"


def test_gates_run_first(steps):
    """The gates must start immediately, or they'd queue behind the build."""
    for step in steps:
        if step.get("id") in GATE_IDS:
            assert step.get("waitFor") == ["-"], f"{step['id']} must start immediately"


def test_every_other_step_waits_for_both_gates(steps):
    """Transitively or directly — no step may start before CI has passed.

    This is the assertion that actually makes the gate un-bypassable: adding a
    new step with `waitFor: ['-']` would let it run alongside the gate, and
    that is precisely the mistake this guards.
    """
    # Cloud Build semantics, which the naive reading gets wrong twice:
    #   * `waitFor` ABSENT  => wait for ALL previously-defined steps (gated,
    #     as long as a gate step appears earlier in the list).
    #   * `waitFor: ['-']`  => start immediately (NOT gated).
    #   * otherwise         => gated iff every named dependency is gated.
    # Steps are also allowed to omit `id`, so index by position, not by id.
    index_of = {s.get("id"): i for i, s in enumerate(steps) if s.get("id")}
    gate_positions = [index_of[g] for g in GATE_IDS if g in index_of]
    earliest_gate = min(gate_positions) if gate_positions else len(steps)

    def reaches_gate(pos: int, seen: set[int]) -> bool:
        step = steps[pos]
        if step.get("id") in GATE_IDS:
            return True
        if pos in seen:
            return False
        seen.add(pos)

        deps = step.get("waitFor")
        if deps is None:
            # Implicitly waits for every earlier step, so it is gated as long
            # as at least one gate is defined before it.
            return pos > earliest_gate
        if deps == ["-"]:
            return False
        return all(d in index_of and reaches_gate(index_of[d], seen) for d in deps)

    offenders = [
        s.get("id") or f"<unnamed step #{i}>"
        for i, s in enumerate(steps)
        if s.get("id") not in GATE_IDS and not reaches_gate(i, set())
    ]
    assert not offenders, (
        f"these steps can start before the CI gate passes: {offenders}. "
        "Add 'ci-gate-backend' and 'ci-gate-frontend' to their waitFor."
    )


def test_skip_substitution_defaults_to_off(pipeline):
    """A push cannot pass substitutions, so the default IS the push behaviour."""
    assert pipeline["substitutions"]["_SKIP_CI_GATE"] == ""


def test_gate_runs_the_same_checks_as_ci(steps):
    """Drift guard: a gate that checks less than CI is a gate in name only."""
    script = "\n".join(" ".join(s.get("args") or []) for s in steps if s.get("id") in GATE_IDS)
    for required in ("ruff check", "ruff format --check", "pytest", "quality:check:fast", "test:run"):
        assert required in script, f"CI gate does not run {required!r}"


def test_gate_honours_the_emergency_override(steps):
    """The hatch must exist and be checked in both gates, or an incident stalls."""
    for step in steps:
        if step.get("id") in GATE_IDS:
            script = " ".join(step.get("args") or [])
            assert "_SKIP_CI_GATE" in script, f"{step['id']} ignores the override"


def test_no_build_step_arg_exceeds_cloud_builds_limit():
    """Cloud Build caps a single step arg at 10,000 characters.

    This is a VALIDATION-time limit: exceeding it fails the whole build before
    any step runs, with a message that names an arg index rather than a cause —
    "invalid build: invalid .steps field: build step 10 arg 1 too long". It cost
    a red deploy on 2026-08-07, and the trigger was innocuous: twelve lines of
    explanatory COMMENT added inside the deploy step's inline bash script, which
    was already at ~9.6K.

    Comments inside a `- |` block count toward the arg. Prose belongs in a
    YAML-level comment above the step, where it costs nothing.

    Asserted at 9,500 rather than 10,000 so there is headroom to notice the
    squeeze before a build fails on it.
    """
    repo_root = Path(__file__).resolve().parents[3]
    config = yaml.safe_load((repo_root / "cloudbuild.yaml").read_text())

    oversized = [
        (i, config["steps"][i].get("id") or f"step-{i}", j, len(str(arg)))
        for i, step in enumerate(config["steps"])
        for j, arg in enumerate(step.get("args", []))
        if len(str(arg)) > 9500
    ]
    assert not oversized, (
        "Cloud Build step arg(s) near or over the 10,000-char limit "
        f"{oversized}. Move explanatory comments OUT of the inline script and "
        "into a YAML comment above the step — they count toward the arg."
    )


def test_comma_valued_env_vars_use_the_delimiter_override(steps, pipeline):
    """A comma-bearing --set-env-vars value MUST use the `^|^` override.

    gcloud treats comma as the KEY=VAL separator, so
    `--set-env-vars=FOO=a,b` parses `b` as a second pair and the deploy dies
    with "Bad syntax for dict arg". cloudbuild.yaml documents this rule (G18,
    after a silently-dropped second service account) — and AUTH_OPERATOR_DOMAINS
    was added four lines below the rule and broke it anyway, failing the first
    dev deploy of TEMPLATE-INVERT.

    Documented rules do not hold; this asserts it. Substitutions whose VALUE is
    comma-bearing are known from the defaults block, so check those.
    """
    import re

    text = CLOUDBUILD.read_text(encoding="utf-8")
    subs = pipeline.get("substitutions", {})
    comma_valued = {k for k, v in subs.items() if isinstance(v, str) and "," in v}
    # AUTH_OPERATOR_DOMAINS is comma-valued by nature (a domain LIST) even when
    # its default here is empty, so it is always in scope.
    comma_valued.add("_AUTH_OPERATOR_DOMAINS")

    offenders = []
    for name in sorted(comma_valued):
        for m in re.finditer(rf"--set-env-vars=(\S*?)\{{{name}\}}", text):
            if "^|^" not in m.group(0):
                offenders.append(f"{name}: {m.group(0)[:70]}")
    assert offenders == [], (
        "comma-valued env vars must use the ^|^ delimiter override, or gcloud "
        f"parses the value as extra KEY=VAL pairs: {offenders}"
    )
