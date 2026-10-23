"""Every build must have an immutable identity, and deploys must pin by digest.

v6.20.0, AIPLA #47. Before this, images were tagged `:${BRANCH_NAME}` and
deployed by that same tag. Two consequences, both invisible until they bite:

  * `:prod` is repushed on every prod deploy, so it names "whatever shipped
    last" rather than a thing. "What is prod running?" was answerable only by
    inferring from a branch tip, and rollback meant rebuild.
  * A concurrent build can move the tag between our push and the
    `gcloud run deploy` that resolves it — the deployed bytes are then not the
    bytes this build produced.

These are STRUCTURAL assertions over the pipelines rather than behavioural
tests, for the same reason as test_cloudbuild_ci_gate.py: the failure mode is
someone adding an image or a deploy line that forgets the digest, and nothing
surfaces that until a rollback is needed and there is nothing to roll back to.

The `--image=` assertions are the load-bearing ones. The tag assertions only
guard the inputs that make them possible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_CLOUDBUILD = REPO_ROOT / "cloudbuild.yaml"
BACKEND_CLOUDBUILD = REPO_ROOT / "backend" / "cloudbuild.yaml"


def all_pipelines() -> list[Path]:
    """EVERY cloudbuild pipeline, found by glob rather than named by hand.

    Downstream feedback #12: v6.20.0 migrated two of four pipelines to
    SHORT_SHA + digest pinning and this guard, which encodes the rule, asserted
    against those same two BY NAME. The other two kept deploying an unguarded
    `:${BRANCH_NAME}` — empty on a tag build, so they could not be built at a
    tag at all — and the guard could not see them. Globbing would have caught it
    in the same sprint that introduced it.
    """
    found = sorted(
        p for p in REPO_ROOT.rglob("cloudbuild*.yaml") if ".venv" not in p.parts and "node_modules" not in p.parts
    )
    assert found, "no cloudbuild pipelines found — did the glob break?"
    return found


# The immutable tag every build must carry. SHORT_SHA is populated on BOTH
# branch- and tag-triggered builds, which is why it — and not TAG_NAME — is the
# one that must always be present. See test_tag_name_is_guarded.
IMMUTABLE_TAG = "${SHORT_SHA}"
MUTABLE_TAG = "${BRANCH_NAME}"

PIPELINES = {
    "root": ROOT_CLOUDBUILD,
    "backend": BACKEND_CLOUDBUILD,
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
    """Flatten a step to searchable text (args may be a list or a script blob)."""
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
    script = step.get("script")
    if isinstance(script, str):
        parts.append(script)
    text = "\n".join(parts)
    return text + "\n" + _inlined_scripts(text)


def _pipeline_text(pipeline: dict) -> str:
    return "\n".join(_step_text(s) for s in pipeline["steps"])


def _without_build_arg_values(text: str) -> str:
    """Drop `--build-arg` VALUES, keeping everything else.

    Both mutable-substitution guards below exist for the tenant failure: an empty
    substitution inside an IMAGE REFERENCE yields `repo:` and the build dies
    with "invalid reference format", an error that never names the cause. Their
    docstrings say so — "in an image reference", "in an image tag".

    A `--build-arg` is not an image reference. An empty one bakes an empty
    string into the image and the consumer decides what that means; v6.44.0's
    `RELEASE_TAG=${TAG_NAME}` does exactly that, deliberately, and the backend
    falls back to COMMIT_SHA when it is empty. Forbidding it was the guard
    being over-broad, not the pipeline being wrong.

    So the exemption is narrow: the ARGUMENT TO `--build-arg` only. A `-t`
    carrying the same substitution is still caught — asserted below.
    """
    lines = text.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines):
        prev = lines[i - 1].strip() if i else ""
        if prev == "--build-arg" and re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*=", line):
            continue
        keep.append(line)
    return "\n".join(keep)


def _image_flags(pipeline: dict) -> list[str]:
    """Every `--image=<ref>` the pipeline passes to gcloud run."""
    return re.findall(r"--image[= ]\s*(\S+)", _pipeline_text(pipeline))


@pytest.fixture(scope="module", params=sorted(PIPELINES))
def named_pipeline(request) -> tuple[str, dict]:
    name = request.param
    return name, _load(PIPELINES[name])


def test_pipeline_files_exist():
    for name, path in PIPELINES.items():
        assert path.is_file(), f"{name} pipeline missing at {path}"


def test_images_are_tagged_immutably(named_pipeline):
    """Every image built must also be tagged with the commit sha.

    Without this there is no way to address an exact build after the branch tag
    has moved on, which is what makes rollback-without-rebuild possible.
    """
    name, pipeline = named_pipeline
    text = _pipeline_text(pipeline)
    built = set(re.findall(r"/([a-z-]+):\$\{SHORT_SHA\}", text))
    assert built, f"{name}: found no images tagged by SHORT_SHA — did the tag scheme change?"
    for image in sorted(built):
        assert f"/{image}:{IMMUTABLE_TAG}" in text, (
            f"{name}: image '{image}' is tagged '{MUTABLE_TAG}' but never '{IMMUTABLE_TAG}'. "
            "An image with only a mutable tag cannot be rolled back to."
        )


def test_branch_tag_is_still_pushed(named_pipeline):
    """Additive change: nothing that reads the branch tag may break.

    Kept as an explicit assertion because 'clean up the old tag' is the obvious
    next edit, and it would silently break any operator or script still
    referencing `:dev`.
    """
    name, pipeline = named_pipeline
    assert MUTABLE_TAG in _pipeline_text(pipeline), (
        f"{name}: the branch tag is no longer pushed — this change is meant to be additive"
    )


def test_deploy_never_references_a_mutable_tag(named_pipeline):
    """The assertion that actually matters: no deploy may resolve a moving tag."""
    name, pipeline = named_pipeline
    flags = _image_flags(pipeline)
    assert flags, f"{name}: no --image flags found — did the deploy step change shape?"
    for ref in flags:
        assert not ref.endswith(f":{MUTABLE_TAG}"), (
            f"{name}: deploy references the mutable tag '{ref}'. Pin the digest instead — "
            "a concurrent build can move that tag between push and deploy."
        )


def test_deploy_pins_by_digest(named_pipeline):
    """Each deployed image must be a digest reference, direct or via a resolved var."""
    name, pipeline = named_pipeline
    for ref in _image_flags(pipeline):
        pinned = "@sha256" in ref or re.search(r"\$\$?\{?[A-Z_]*IMAGE", ref) is not None
        assert pinned, f"{name}: deploy image '{ref}' is neither a digest nor a resolved image variable"


def test_digest_resolution_fails_loud(named_pipeline):
    """An unresolved digest must fail the build, never fall back to the tag.

    A silent fallback would restore the old behaviour precisely when something
    is already wrong, which is the worst moment to become permissive.
    """
    name, pipeline = named_pipeline
    text = _pipeline_text(pipeline)
    assert "RepoDigests" in text or "crane digest" in text, f"{name}: nothing resolves an image digest"
    assert re.search(r"could not resolve digest|digest.*empty|FATAL.*digest", text, re.I), (
        f"{name}: digest resolution has no explicit failure path — it must exit non-zero, "
        "not deploy the mutable tag as a fallback"
    )


@pytest.mark.parametrize("mutable", ["TAG_NAME", "BRANCH_NAME"])
def test_mutable_ref_substitutions_are_guarded(named_pipeline, mutable):
    """Both mutable ref names are conditionally EMPTY, and the emptiness is symmetric:

        branch build -> TAG_NAME    is empty
        tag build    -> BRANCH_NAME is empty

    Either one used unguarded in an image reference yields `image:` and the build
    dies with `invalid reference format`, an error that never mentions the
    substitution that caused it.

    The BRANCH_NAME half is not hypothetical. The first v6.20.0 tag build failed
    exactly here: `…/backend:` on `-t`, because the long-standing
    `-t …:${BRANCH_NAME}` line had only ever run on branch pushes and became
    fatal the moment a tag trigger existed. The original version of this test
    guarded TAG_NAME only — it was written from the same blind spot that caused
    the bug.

    `SHORT_SHA` is populated on BOTH (confirmed on the failed tag build:
    SHORT_SHA=b77c62b, TAG_NAME=v6.20.0, BRANCH_NAME absent), which is why it is
    the one tag applied unconditionally at build time.
    """
    name, pipeline = named_pipeline
    for step in pipeline["steps"]:
        # Comments explaining WHY these are dangerous must not trip the guard —
        # the same false positive the `builds submit` check hit in M3.
        text = "\n".join(line for line in _step_text(step).splitlines() if not line.strip().startswith("#"))
        text = _without_build_arg_values(text)
        if mutable not in text:
            continue
        assert re.search(rf'-n\s+"?\$\$?\{{?{mutable}', text), (
            f"{name}: step '{step.get('id')}' uses {mutable} without an -n guard. "
            f"{mutable} is empty on the opposite trigger kind and produces an "
            "invalid image reference."
        )


def test_build_steps_tag_only_with_the_always_present_substitution(named_pipeline):
    """`docker build -t` cannot be made conditional, so it may only use SHORT_SHA.

    Mutable names belong in the push step, where a shell guard is possible.
    """
    name, pipeline = named_pipeline
    for step in pipeline["steps"]:
        step_id = step.get("id") or ""
        if not step_id.startswith("build-"):
            continue
        text = _without_build_arg_values(_step_text(step))
        for mutable in ("BRANCH_NAME", "TAG_NAME"):
            assert f"{mutable}" not in text, (
                f"{name}: build step '{step_id}' references {mutable} in an image tag. "
                "A build step cannot guard an empty substitution — tag with SHORT_SHA "
                "here and apply mutable names in the push step."
            )


class TestEveryPipelineIsTagSafe:
    """The rule applies to ALL pipelines, not the two that were named here."""

    def test_no_pipeline_deploys_an_unguarded_branch_name(self):
        """`:${BRANCH_NAME}` is legal ONLY inside an `if [ -n "${BRANCH_NAME}" ]`.

        The mutable tag is still pushed on branch builds — things read it — so
        the rule is not "never mention it" but "never use it unguarded". The
        first version of this check flagged the correctly-guarded pushes in the
        root and backend pipelines, and its own explanatory comment.
        """
        offenders = []
        for path in all_pipelines():
            depth = None
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if 'if [ -n "${BRANCH_NAME}" ]' in stripped:
                    depth = line.index("if")
                    continue
                # The guard block ends at the matching `fi` (same indent).
                if depth is not None and stripped == "fi" and line.index("fi") == depth:
                    depth = None
                    continue
                if ":${BRANCH_NAME}" in stripped and depth is None:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        assert offenders == [], (
            "pipelines deploy an unguarded :${BRANCH_NAME}, which is EMPTY on a "
            f"tag build and cannot be promoted: {offenders}"
        )

    def test_every_pipeline_carries_the_immutable_tag(self):
        missing = [
            str(p.relative_to(REPO_ROOT))
            for p in all_pipelines()
            if IMMUTABLE_TAG not in p.read_text(encoding="utf-8")
            # promote copies by digest and builds nothing, so it needs no tag.
            and "promote" not in p.name
        ]
        assert missing == [], f"pipelines with no {IMMUTABLE_TAG} image tag: {missing}"


class TestEveryDeployingPipelineGuardsItsServiceName:
    """A placeholder service name must fail the build, in EVERY pipeline.

    Deploying under the wrong service name does not error — Cloud Run happily
    creates a NEW service beside the live one and the old one keeps serving.
    That is a silent split-brain, and on 2026-08-17 it happened for real: the
    root pipeline had the guard, `backend/cloudbuild.yaml` did not, and the
    first deploy after the identity scrub stood up a stray `platform-backend`.

    Same shape as downstream feedback #12 — a rule enforced on some files and
    not all of them — so it is asserted across the glob rather than per file.
    """

    PLACEHOLDER_PREFIX = "platform-"

    def _deploying_pipelines(self) -> list[Path]:
        out = []
        for path in all_pipelines():
            text = path.read_text(encoding="utf-8")
            # Only pipelines that actually run `gcloud run deploy` need it;
            # promote copies by digest and builds nothing.
            if "run deploy" in text or "'deploy'" in text:
                out.append(path)
        return out

    def test_placeholder_service_name_is_guarded(self):
        offenders = []
        for path in self._deploying_pipelines():
            text = path.read_text(encoding="utf-8")
            declared = re.search(r"^\s+_SERVICE_NAME:\s*(\S+)", text, re.M)
            if not declared or not declared.group(1).startswith(self.PLACEHOLDER_PREFIX):
                continue  # no placeholder default to guard against
            if f'= "{declared.group(1)}"' not in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} (_SERVICE_NAME={declared.group(1)})")
        assert offenders == [], (
            "pipelines default _SERVICE_NAME to a placeholder with no guard — a "
            f"missing substitution would create a duplicate service: {offenders}"
        )


class TestTheBuildArgExemptionIsNarrow:
    """The 2026-09-05 narrowing must not have disarmed the guard.

    `_without_build_arg_values` exempts a `--build-arg` value so v6.44.0's
    `RELEASE_TAG=${TAG_NAME}` can pass. These assert the exemption is exactly
    that wide and no wider — an image tag carrying the same substitution is
    still fatal, which is the bug the guard was written for (the first v6.20.0
    tag build, `…/backend:` on `-t`).
    """

    def test_a_build_arg_value_is_exempt(self):
        step = {"id": "build-x", "args": ["build", "--build-arg", "RELEASE_TAG=${TAG_NAME}", "-t", "repo:${SHORT_SHA}"]}
        assert "TAG_NAME" not in _without_build_arg_values(_step_text(step))

    @pytest.mark.parametrize("mutable", ["TAG_NAME", "BRANCH_NAME"])
    def test_an_image_tag_is_still_caught(self, mutable):
        step = {"id": "build-x", "args": ["build", "-t", f"repo:${{{mutable}}}"]}
        assert mutable in _without_build_arg_values(_step_text(step))

    def test_a_bare_substitution_after_build_arg_is_still_caught(self):
        """Only `NAME=value` is a build-arg assignment. A loose token is not,
        and exempting it would let `--build-arg` become a laundering prefix."""
        step = {"id": "build-x", "args": ["build", "--build-arg", "${TAG_NAME}"]}
        assert "TAG_NAME" in _without_build_arg_values(_step_text(step))

    def test_only_the_line_directly_after_build_arg_is_exempt(self):
        step = {"id": "build-x", "args": ["build", "--build-arg", "A=1", "-t", "repo:${TAG_NAME}"]}
        assert "TAG_NAME" in _without_build_arg_values(_step_text(step))
