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
SPEC_SCRIPT = REPO_ROOT / "scripts" / "deploy-run-service.sh"

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
    # v6.44.0. `run deploy` arrives here because the deploy step now delegates
    # to scripts/deploy-run-service.sh, which this file's _step_text folds in —
    # it is the same invocation cloudbuild.yaml has run on every release since
    # bring-up, so it is verified by several hundred green builds rather than by
    # a --help read. `builds triggers describe` was run against the real SDK on
    # 2026-09-04 (it is how the target env's substitutions were read to build
    # this change) and is the resolve-spec-config step's only command.
    "run deploy",
    "builds triggers describe",
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


# A pipeline step may now DELEGATE its body to a repo script — v6.44.0 moved the
# ~170-line service spec out of cloudbuild.yaml into scripts/deploy-run-service.sh
# so cloudbuild.promote.yaml could call the same one instead of duplicating it.
# Every guard in this file scans pipeline text, so the text has to follow the
# code: without this, extracting a step silently emptied the thing being
# guarded and the assertions passed by finding nothing. Resolve any referenced
# in-repo script and fold its contents in.
_SCRIPT_REF = re.compile(r"(?:^|[\s'\"])((?:scripts|build_scripts)/[\w./-]+\.sh)")


def _inlined_scripts(text: str) -> str:
    bodies: list[str] = []
    for rel in dict.fromkeys(_SCRIPT_REF.findall(text)):
        path = REPO_ROOT / rel
        if path.is_file():
            bodies.append(path.read_text(encoding="utf-8"))
    return "\n".join(bodies)


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
    text = "\n".join(parts)
    return text + "\n" + _inlined_scripts(text)


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
    phrases = set(re.findall(r"gcloud (run services \w+|builds triggers \w+|run \w+)", text))
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


def test_deploy_delegates_to_the_one_shared_spec_script(steps):
    """The service spec has ONE definition, and promotion uses it.

    This test used to assert the opposite — `services update`, images only, no
    `--set-env-vars`, no `--set-secrets` — on the reasoning that restating ~30
    env vars and the secret set here would drift from cloudbuild.yaml. That was
    right about duplication and wrong about what to do instead. The consequence
    surfaced on 2026-09-04: prod had shipped by promotion since v6.34.1, so its
    SPEC had had no deploy route at all, and it was found running with no
    `toolbox` container and five env vars the deployed code reads simply absent.

    So the rule is no longer "promotion must not carry the spec". It is
    "there must be only one copy of the spec". Both pipelines call
    scripts/deploy-run-service.sh; neither may grow its own inline copy.
    """
    deploy = next(s for s in steps if s.get("id") == "deploy")
    args = "\n".join(str(a) for a in deploy.get("args", []))
    assert SPEC_SCRIPT.name in args, f"the deploy step must call {SPEC_SCRIPT.name} rather than deploying by hand"
    assert "--set-env-vars" not in args, (
        "the env surface belongs in the shared script, not inline here — "
        "an inline copy is exactly the drift this indirection exists to prevent"
    )
    assert "--set-secrets" not in args


def test_both_pipelines_call_the_same_spec_script():
    """Release and promotion must deploy the same shape, or test proves nothing."""
    assert SPEC_SCRIPT.is_file(), f"{SPEC_SCRIPT} is missing"
    release = yaml.safe_load((REPO_ROOT / "cloudbuild.yaml").read_text(encoding="utf-8"))
    release_deploy = next(s for s in release["steps"] if s.get("id") == "deploy")
    release_args = "\n".join(str(a) for a in release_deploy.get("args", []))
    assert SPEC_SCRIPT.name in release_args, (
        "cloudbuild.yaml's deploy step no longer calls the shared script — "
        "if the spec moved back inline, promotion is deploying a different service"
    )


def test_promotion_carries_rules_indexes_and_the_skill_seed(steps):
    """Everything a release applies to an env, a promotion must apply too.

    Each of these was release-only, and each left prod behind in a way nothing
    reported: rules and indexes reached prod only from a branch push, and the
    seed never ran there at all — which is why prod was missing `document-writer`
    on 2026-09-04 while test had it.
    """
    ids = {s.get("id") for s in steps}
    assert "deploy-firebase-rules" in ids
    assert "seed-platform-skills" in ids
    seed = next(s for s in steps if s.get("id") == "seed-platform-skills")
    assert "smoke" in set(seed.get("waitFor", [])), "seed must run only against a healthy target"


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


def _sidecar_loops(steps: list[dict]) -> tuple[set[str], set[str]]:
    """The service lists in copy-sidecars and deploy-sidecars."""
    copy = _step_text(next(s for s in steps if s.get("id") == "copy-sidecars"))
    deploy = _step_text(next(s for s in steps if s.get("id") == "deploy-sidecars"))
    copied = {p.split(":")[0] for p in re.search(r"for PAIR in (.+?); do", copy).group(1).split()}
    deployed = set(re.search(r"for SVC in (.+?); do", deploy).group(1).split())
    return copied, deployed


def test_the_two_sidecar_loops_stay_in_step(steps):
    """Copy and deploy must name the same services.

    They are two lists in two steps, and the only thing keeping them equal is a
    comment asking nicely. A service copied but not deployed wastes a registry
    push; a service deployed but not copied reads a /workspace file that was
    never written.
    """
    copied, deployed = _sidecar_loops(steps)
    assert copied == deployed, (
        f"copy-sidecars has {sorted(copied)} but deploy-sidecars has {sorted(deployed)} — the two loops have drifted"
    )


def test_every_separately_built_service_is_promoted(steps):
    """The list that decides what a version actually DESCRIBES.

    A service the root release tag does not build has no promotion path unless it
    is named here, and nothing reports the omission — the promotion goes green
    having silently skipped it. On 2026-09-04 `platform-v6-backend` was found on
    prod running v6.34.0, TEN versions behind, moments after a v6.44.0 promotion
    reported success. It was missed because root cloudbuild.yaml builds a
    container called `backend` (the FRONTEND's sidecar), so the standalone
    service looked covered.

    `mcp-ext-apps-map` is deliberately absent: on prod it is still the
    `cloudrun/container/hello` bootstrap, and activating a publicly-invokable MCP
    there must be a decision rather than a side effect of shipping something
    else. Adding it here IS that decision — make it knowingly.
    """
    copied, _ = _sidecar_loops(steps)
    assert "platform-v6-backend" in copied, (
        "the standalone backend (A2A + channels) is not promoted — it will sit at "
        "whatever version last reached it by branch push, which is how it ended up "
        "ten versions behind on prod"
    )
    assert {"platform-browser", "mcp-sandbox"} <= copied
    assert "mcp-ext-apps-map" not in copied, (
        "mcp-ext-apps-map is excluded ON PURPOSE — see the comment in copy-sidecars. "
        "If you are activating it on prod deliberately, update this test and that "
        "comment together."
    )


def test_promote_supplies_every_substitution_the_spec_script_requires(pipeline):
    """`deploy-run-service.sh` refuses to run unless every var it names is SET.

    Both pipelines delegate to that one script, but they populate it
    differently: `cloudbuild.yaml` forwards its substitutions directly, while a
    promotion rebuilds the environment in `resolve-spec-config` from a
    `spec_defaults.json` literal. So a var added to the script and to
    cloudbuild.yaml is still missing here, and nothing said so — the promotion
    builds all three images, pushes them, and dies at the deploy step.

    That is not hypothetical: promoting v6.49.0 to prod on 2026-09-11 failed
    with `called without: _RESIDENT_SESSION_STORY_ENABLED`, added to the script
    and cloudbuild.yaml by the M7 arc (7c15f74c) and never to this pipeline.
    Prod was unreachable by promotion from that commit until this was fixed.

    The merge in `resolve-spec-config` also intersects inherited keys against
    the defaults, so a key absent from `spec_defaults.json` cannot be supplied
    by the source trigger either — the literal is the only place that can fix it.
    """
    required = set(re.findall(r"(_[A-Z0-9_]+)", _spec_script_required_block()))

    resolve = next(s for s in pipeline["steps"] if s.get("id") == "resolve-spec-config")
    body = "\n".join(resolve["args"])
    # Keys of the spec_defaults.json literal: `"_KEY": "${_KEY}"`.
    supplied = set(re.findall(r'"(_[A-Z0-9_]+)"\s*:', body))

    missing = required - supplied
    assert not missing, (
        f"scripts/deploy-run-service.sh requires {sorted(missing)}, which "
        f"cloudbuild.promote.yaml's spec_defaults does not supply. A promotion "
        f"would build and push every image, then fail at the deploy step. Add "
        f"each to BOTH the substitutions block and the spec_defaults literal."
    )


def _spec_script_required_block() -> str:
    """The `for v in … ; do` list that deploy-run-service.sh checks for unset vars.

    Sliced out rather than matched across the whole file so an unrelated
    mention of a substitution elsewhere in the script cannot inflate the set.
    """
    text = SPEC_SCRIPT.read_text(encoding="utf-8")
    start = text.index("for v in _PROJECT_ID")
    end = text.index("do\n", start)
    return text[start:end]
