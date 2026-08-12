"""cloudbuild.promote.yaml — build-once artifact promotion (M2, AIPLA #46/#47).

AIPLA's promote pipeline sat committed, reviewed, and documented for SIX WEEKS
before its first real run — which failed immediately on
`gcloud artifacts docker images copy`, a command that does not exist in any
SDK version (not in the cloud-builders gcloud, not in SDK 557.0.0: `gcloud
artifacts docker images` offers only delete / describe / get-operation /
list / list-vulnerabilities / scan — verified locally against the installed
SDK). Two further latent bugs surfaced in that same first run. The lesson:
"it parses" and "it has run" are different claims, and this file is the
former — the milestone's actual acceptance gate is one REAL dev->test
promotion (M4), not this test passing.

What this test CAN catch, and is scoped to: the shape of the pipeline
(copies two images, rebuilds one; deploy waits on all three; no config
surface reasserted) and every gcloud/crane subcommand actually being real,
checked against a verified allowlist rather than trusted from memory — the
exact class of defect that cost AIPLA six weeks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMOTE = REPO_ROOT / "cloudbuild.promote.yaml"

# Verified 2026-07-31 against the EXACT pinned builder images this pipeline
# uses (not trusted from memory — that is the whole point of this test):
#   * `docker run --rm --entrypoint crane gcr.io/go-containerregistry/crane:debug
#     --help` -> Available Commands: append, auth, blob, catalog, completion,
#     config, copy, delete, digest, export, flatten, help, index, ls,
#     manifest, mutate, pull, push, rebase, registry, tag, validate, version.
#   * `gcloud run services update --help` / `gcloud run services describe --help`
#     against local SDK 557.0.0 — both exist and take --container/--image.
# Re-verify this way (not from memory) whenever a new gcloud/crane invocation
# is added to this pipeline — that is the entire lesson of #46/#47.
VERIFIED_CRANE_SUBCOMMANDS = {"digest", "copy"}
VERIFIED_GCLOUD_SUBCOMMAND_PHRASES = {
    "run services update",
    "run services describe",
}
# The exact command AIPLA's pipeline shipped and that does not exist anywhere.
# If this ever appears in our pipeline, the test must fail — hence the
# explicit negative assertion below rather than relying on it simply being
# absent from the verified set.
NONEXISTENT_COMMAND = "gcloud artifacts docker images copy"


@pytest.fixture(scope="module")
def pipeline() -> dict:
    return yaml.safe_load(PROMOTE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(pipeline: dict) -> list[dict]:
    return pipeline["steps"]


def _step_text(step: dict) -> str:
    parts: list[str] = []
    for key in ("id", "name", "entrypoint"):
        value = step.get(key)
        if isinstance(value, str):
            parts.append(value)
    args = step.get("args")
    if isinstance(args, list):
        parts.extend(str(a) for a in args)
    elif isinstance(args, str):
        parts.append(args)
    return "\n".join(parts)


def _pipeline_text(pipeline: dict) -> str:
    return "\n".join(_step_text(s) for s in pipeline["steps"])


def test_file_exists():
    assert PROMOTE.is_file(), "cloudbuild.promote.yaml is missing"


def test_the_nonexistent_command_never_appears(pipeline):
    """The literal regression check: AIPLA's dead command must never reappear."""
    assert NONEXISTENT_COMMAND not in _pipeline_text(pipeline)


def test_requires_explicit_version(steps):
    guard = next(s for s in steps if s.get("id") == "guard-version")
    text = _step_text(guard)
    assert "_VERSION" in text
    assert re.search(r'-z\s+"?\$\{?_VERSION', text), (
        "guard-version must refuse to run without an explicit _VERSION — "
        "a promote without a frozen tag is a rebuild wearing a promote's clothes"
    )


def test_every_invoked_crane_subcommand_is_verified(pipeline):
    text = _pipeline_text(pipeline)
    used = set(re.findall(r"crane (\w+)", text))
    assert used, "no crane invocations found — did the copy mechanism change?"
    unverified = used - VERIFIED_CRANE_SUBCOMMANDS
    assert not unverified, (
        f"crane subcommand(s) {unverified} are not in the verified allowlist. "
        "Verify against the ACTUAL pinned image "
        "(docker run --rm --entrypoint crane gcr.io/go-containerregistry/crane:debug --help) "
        "before adding — do not trust memory for this class of bug."
    )


def test_every_invoked_gcloud_phrase_is_verified(pipeline):
    text = _pipeline_text(pipeline)
    # Extract "gcloud <2-3 word phrase>" occurrences from the deploy/smoke steps.
    phrases = set(re.findall(r"gcloud (run services \w+|run \w+)", text))
    assert phrases, "no gcloud invocations found — did the deploy mechanism change?"
    unverified = {p for p in phrases if p not in VERIFIED_GCLOUD_SUBCOMMAND_PHRASES}
    assert not unverified, (
        f"gcloud phrase(s) {unverified} are not in the verified allowlist. "
        "Verify with `gcloud <phrase> --help` against the real SDK before adding."
    )


def test_copies_exactly_two_images_and_rebuilds_exactly_one(steps):
    """Backend + toolbox are copy-promotable (no baked env state); the UI is not
    (NEXT_PUBLIC_* is compile-time-inlined and environment-specific)."""
    ids = {s.get("id") for s in steps}
    assert {"copy-backend", "copy-toolbox"} <= ids
    assert "build-frontend" in ids
    # Negative: nothing should attempt to copy the UI image.
    for step in steps:
        if step.get("id", "").startswith("copy-"):
            assert "/ui:" not in _step_text(step), (
                "the frontend must never be copy-promoted — it bakes "
                "environment-specific NEXT_PUBLIC_* values at compile time"
            )


def test_digest_equality_is_asserted_after_each_copy(steps):
    for step_id in ("copy-backend", "copy-toolbox"):
        step = next(s for s in steps if s.get("id") == step_id)
        text = _step_text(step)
        assert re.search(r"DIGEST.*!=.*DST_DIGEST|DST_DIGEST.*!=.*DIGEST", text), (
            f"{step_id} does not verify the destination digest matches the source. "
            "A copy that silently retagged something else would deploy unverified bytes."
        )
        assert re.search(r"FATAL.*digest changed", text, re.I), (
            f"{step_id} has no explicit failure path on a digest mismatch"
        )


def test_copies_write_a_canonical_repo_at_digest_reference(steps):
    """The image file must hold `repo@sha256:…`, never `repo:tag@sha256:…`.

    Caught in verification, not by the original tests: `DST` carried the
    version tag, so composing "$DST@$DIGEST" produced the tagged-AND-digested
    form. The docker CLI accepts it, which is exactly why it is dangerous —
    it looks fine everywhere except the one place it is consumed
    (`gcloud run services update --image`), and the first real promotion is a
    terrible time to discover a reference-parsing disagreement.
    """
    for step_id, artifact in (("copy-backend", "backend"), ("copy-toolbox", "toolbox")):
        step = next(s for s in steps if s.get("id") == step_id)
        text = _step_text(step)
        write = re.search(rf"echo\s+\"([^\"]+)\"\s*>\s*/workspace/image_{artifact}", text)
        assert write, f"{step_id} does not write /workspace/image_{artifact}"
        reference = write.group(1)
        assert reference.endswith("@$${DIGEST}"), f"{step_id} must write a repo@digest reference, got {reference}"

        # The tag lives in the shell VARIABLE, not in this literal, so checking
        # the literal for a version is vacuous — the first version of this test
        # did exactly that and passed against the very bug it was written to
        # catch. Resolve the variable and assert ITS definition carries no tag.
        var = re.match(r"\$\$\{(\w+)\}@", reference)
        assert var, f"{step_id} reference does not start with a shell variable: {reference}"
        var_name = var.group(1)
        assignment = re.search(rf'^\s*{var_name}="([^"]+)"', text, re.M)
        assert assignment, f"{step_id} has no assignment for {var_name}"
        assert "_VERSION" not in assignment.group(1), (
            f"{step_id} writes a tagged-AND-digested reference: {var_name}="
            f"{assignment.group(1)} yields repo:tag@sha256. Compose the digest "
            "reference from the untagged repo path instead."
        )


def test_deploy_waits_on_all_three_artifacts(steps):
    deploy = next(s for s in steps if s.get("id") == "deploy")
    wait_for = set(deploy.get("waitFor", []))
    assert {"copy-backend", "copy-toolbox", "push-frontend"} <= wait_for, (
        "deploy must wait for both copies AND the frontend push — "
        "a reordering here would deploy before the copy verified its digest"
    )


def test_deploy_reasserts_no_env_or_secret_surface(steps):
    """`services update` — not `run deploy` — and images only.

    cloudbuild.yaml's deploy step re-asserts ~25 env vars + the full secret
    set on every deploy. Duplicating that surface in the promote pipeline
    would invite drift between the two; `services update --image` changes
    only the named container's image and leaves the rest (set at env-cut,
    owned by Terraform) untouched.
    """
    deploy = next(s for s in steps if s.get("id") == "deploy")
    text = _step_text(deploy)
    assert "services update" in text
    assert "--set-env-vars" not in text
    assert "--set-secrets" not in text


def test_deploy_pins_all_three_containers_by_digest(steps):
    deploy = next(s for s in steps if s.get("id") == "deploy")
    text = _step_text(deploy)
    for container in ("main", "sidecar", "toolbox"):
        assert f"--container={container}" in text, f"deploy is missing the {container} container"
    # Each --image right after a --container must resolve a var populated
    # from a digest reference (image_backend / image_toolbox / image_ui),
    # never a bare tag.
    assert re.search(r"--image=\${1,2}\{?UI_IMAGE", text)
    assert re.search(r"--image=\${1,2}\{?BACKEND_IMAGE", text)
    assert re.search(r"--image=\${1,2}\{?TOOLBOX_IMAGE", text)


def test_smoke_step_fails_the_build_on_any_non_200(steps):
    smoke = next(s for s in steps if s.get("id") == "smoke")
    text = _step_text(smoke)
    assert "exit $" in text or "exit ${fail}" in text or "exit $${fail}" in text
    assert "fail=1" in text


def test_no_ci_gate(steps):
    """Deliberate: the artifact is frozen and was gated at the source build.
    Re-running the CI gate here would test the same bytes twice for nothing.
    """
    ids = {s.get("id") for s in steps}
    assert not any("ci-gate" in i for i in ids if i)
