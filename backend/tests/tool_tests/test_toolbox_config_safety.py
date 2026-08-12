"""Static safety gates on every shipped MCP Toolbox ``tools.yaml``.

These tests encode the findings of the 2026-07-17 Toolbox spike (v1.7.0, run
against ONE's real BigQuery datasets). They are STATIC — no Toolbox process, no
BigQuery — so they run in CI on every push. The live behavioural proof lives in
``tests/integration/test_toolbox_live.py`` (marked ``integration``).

Why these exist (design doc: docs/design/v6.14.0/mcp-toolbox-database-gateway.md):

  * Toolbox's ``allowedValues`` on a ``templateParameter`` is a **substring**
    match, not equality. ``"junk Day-Ahead Price DK1 junk"`` passes the check
    because it *contains* an allowed value, and is then interpolated RAW into
    the SQL statement. The spike weaponised this to read a column excluded from
    the allowlist and, via a scalar subquery, a table in a different dataset.
  * ``allowedDatasets`` does NOT gate hand-authored ``bigquery-sql`` tools — it
    is only validated at boot that the listed datasets exist. So there is no
    second line of defence behind a templateParameter.
  * The connecting identity's IAM is therefore the only backstop, and it is
    PROJECT-level ``bigquery.dataViewer`` on ``your-entsoe-project`` — i.e.
    an injection reaches all six of ONE's datasets.

Conclusion: ``templateParameters`` is an un-mitigated SQL-injection vector in
our configuration, so it is banned outright rather than used carefully. Column
choice uses ``CASE`` on a bound param (C2); table choice uses ``UNION ALL`` with
a literal label filtered by a bound param (C2b).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# infrastructure/mcp-toolbox/ lives at the repo root, two levels above backend/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLBOX_DIR = _REPO_ROOT / "infrastructure" / "mcp-toolbox"


def _shipped_config_paths() -> list[Path]:
    """Every Toolbox config we ship. Empty list is a failure, not a pass.

    Globs ``tools*.yaml`` so it covers BOTH the real ``tools.yaml`` (client
    queries, excluded from the public template) AND ``tools.example.yaml``
    (which ships in the template). The example must obey the same rules — a
    template that demonstrated ``templateParameters`` would teach every forker
    the injection vector.
    """
    return sorted(_TOOLBOX_DIR.glob("**/tools*.yaml"))


def _load_documents(path: Path) -> list[dict]:
    """Parse a multi-document Toolbox config into a list of mappings."""
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def _tools(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d.get("kind") == "tool"]


def _sources(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d.get("kind") == "source"]


def test_at_least_one_config_is_shipped() -> None:
    """Guard the guard: if the glob silently matches nothing, every test below
    would vacuously pass and the injection gate would be decorative."""
    assert _shipped_config_paths(), (
        f"no tools.yaml found under {_TOOLBOX_DIR} — the safety gates below would vacuously pass"
    )


def test_local_and_deployed_toolbox_versions_match() -> None:
    """The binary `make dev` installs must be the same Toolbox the sidecar runs.

    Deployed, Toolbox is the pinned image in `infrastructure/mcp-toolbox/Dockerfile`.
    Locally it is the binary fetched by `scripts/install-toolbox.sh`. If those
    drift, local dev proves nothing about production — and the drift would be
    invisible until a version-specific behaviour differed (this project's config
    format and its `allowedValues` semantics have BOTH changed across versions).
    """
    dockerfile = (_TOOLBOX_DIR / "Dockerfile").read_text()
    installer = (_REPO_ROOT / "scripts" / "install-toolbox.sh").read_text()

    image_tag = re.search(r"^FROM\s+\S+/toolbox:(\S+)\s*$", dockerfile, re.MULTILINE)
    assert image_tag, "could not find the pinned `FROM …/toolbox:<tag>` line in infrastructure/mcp-toolbox/Dockerfile"

    script_version = re.search(r'^TOOLBOX_VERSION="([^"]+)"', installer, re.MULTILINE)
    assert script_version, "could not find TOOLBOX_VERSION in scripts/install-toolbox.sh"

    assert image_tag.group(1) == script_version.group(1), (
        f"Toolbox version drift: Dockerfile pins {image_tag.group(1)!r} but "
        f"scripts/install-toolbox.sh installs {script_version.group(1)!r}. Local dev would "
        "exercise a different Toolbox than the deployed sidecar."
    )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_config_parses_as_multi_document_yaml(path: Path) -> None:
    docs = _load_documents(path)
    assert docs, f"{path} parsed to zero documents"
    for doc in docs:
        assert "kind" in doc, f"{path}: every document needs a `kind` discriminator, got {doc!r}"


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_no_template_parameters_anywhere(path: Path) -> None:
    """C2/C2b: ``templateParameters`` is BANNED — it is a raw-interpolation
    SQL-injection vector whose only guard (``allowedValues``) is a bypassable
    substring match.

    This is the test that must fail if someone reintroduces the vector.
    """
    offenders = [t["name"] for t in _tools(_load_documents(path)) if "templateParameters" in t]
    assert not offenders, (
        f"{path}: tools {offenders} use `templateParameters`, which interpolates caller input "
        "RAW into SQL. Its `allowedValues` guard is a SUBSTRING match and is bypassable "
        "(spike 2026-07-17). Use CASE on a bound param for columns (C2), or UNION ALL with a "
        "literal label filtered by a bound param for tables (C2b)."
    )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_raw_text_contains_no_template_interpolation(path: Path) -> None:
    """Belt and braces: catch Go-template interpolation (``{{.foo}}``) even if it
    appears somewhere the structured check above doesn't reach."""
    raw = path.read_text()
    assert "{{." not in raw, (
        f"{path}: contains Go-template interpolation ('{{{{.'), which substitutes caller input "
        "directly into SQL. Banned — see C2/C2b in the design doc."
    )


_GENERIC_EXECUTORS = {
    "bigquery-execute-sql",
    "postgres-execute-sql",
    "mysql-execute-sql",
    "spanner-execute-sql",
    "sqlite-execute-sql",
}


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_generic_sql_executors_are_bound_to_a_scoped_source(path: Path) -> None:
    """C3 **as amended by v6.23.0** (docs/design/v6.23.0/one-bigquery.md).

    The original C3 banned generic executors outright, citing Google's
    "not for production agents" note. That conflated two things, and the half
    that matters is inverted:

      * For a hand-authored ``bigquery-sql`` tool, ``allowedDatasets`` is NOT
        enforced per query — only checked at boot. IAM is the sole backstop.
      * For the GENERIC executor, ``allowedDatasets`` IS enforced per query.
        Proven live in ``tests/integration/test_toolbox_live.py``.

    So on dataset scope the generic executor is *stricter* than what C3 permitted.
    The amended rule is not "never ship one" but "never ship an UNSCOPED one":
    a generic executor may ship iff its source declares a non-empty
    ``allowedDatasets``, ``writeMode: blocked``, and a ``maxQueryResultRows`` cap.

    Note this still effectively bans the non-BigQuery executors — no other source
    type has an ``allowedDatasets`` equivalent, so they cannot satisfy the rule.
    That is intended, not an accident of the implementation.

    ``--prebuilt`` remains banned separately and absolutely: it hardwires its own
    unscoped source, and Toolbox 1.7.0 itself logs that the prebuilt configs are
    "not secure enough for 'run time' use cases".
    """
    docs = _load_documents(path)
    sources_by_name = {s["name"]: s for s in _sources(docs)}

    for tool in _tools(docs):
        if tool.get("type") not in _GENERIC_EXECUTORS:
            continue
        name = tool["name"]
        source = sources_by_name.get(tool.get("source"))
        assert source is not None, (
            f"{path}: generic executor {name!r} references undeclared source {tool.get('source')!r}"
        )

        allowed = source.get("allowedDatasets")
        assert allowed, (
            f"{path}: generic executor {name!r} is bound to source {source['name']!r} with no "
            "`allowedDatasets`. That source's reach is the service account's FULL IAM — for "
            "sa-platform that is every dataset in the customer's project. An unscoped generic "
            "executor is exactly what amended-C3 forbids."
        )
        assert source.get("writeMode") == "blocked", (
            f"{path}: generic executor {name!r} is bound to source {source['name']!r} with "
            f"writeMode={source.get('writeMode')!r}. A generic executor without a blocked write "
            "mode lets the model author arbitrary DML/DDL."
        )
        assert source.get("maxQueryResultRows"), (
            f"{path}: generic executor {name!r} is bound to source {source['name']!r} with no "
            "`maxQueryResultRows` cap — an unbounded result can blow the context window and "
            "trip the >50K artifact offload."
        )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_bigquery_sources_take_their_project_from_the_environment(path: Path) -> None:
    """The BigQuery billing/job project must be runtime-env-driven, not baked.

    v6.20.0 build-once promotion COPIES the toolbox image to test/prod by digest,
    and ``tools.yaml`` is baked into that image — so a literal project id in this
    file is the SAME id in every environment. Before v6.23.0 that meant test and
    prod ran BigQuery jobs billed to, and against the query quota of, the dev
    project. Runtime env is the only mechanism that survives promotion.

    The value must also carry a COLON-default. Toolbox exits(1) on an unset
    variable, and that parse failure fails the sidecar's Cloud Run startup probe,
    which takes the whole (public) frontend revision Ready:False. A default turns
    a missing var into old-behaviour drift instead of an outage.

    NOTE the exact syntax: Toolbox uses ``{VAR:default}``, NOT shell's
    ``{VAR:-default}`` — given the latter it reads the default as
    ``-<project>`` and BigQuery rejects the leading dash. Verified 2026-08-07.
    """
    for source in _sources(_load_documents(path)):
        if source.get("type") != "bigquery":
            continue
        project = str(source.get("project", ""))
        name = source.get("name")
        assert project.startswith("${") and project.endswith("}"), (
            f"{path}: bigquery source {name!r} has a literal project {project!r}. "
            "tools.yaml is baked into an image that promotion copies by digest, so a "
            "literal value bills every environment's BigQuery jobs to that one project. "
            "Use the environment form with a default instead."
        )
        assert ":" in project and ":-" not in project, (
            f"{path}: bigquery source {name!r} project {project!r} has no colon-default "
            "(or uses shell's ':-' form, which Toolbox misreads as a leading-dash default). "
            "Without a default, an unset variable makes Toolbox exit(1) at boot and takes "
            "the public frontend revision Ready:False."
        )


def test_cloudbuild_sets_the_toolbox_bigquery_project() -> None:
    """The other half of the test above: a default is only a safety net if the
    real value is actually supplied on the deploy path that establishes env config.

    Promotion (`cloudbuild.promote.yaml`) deliberately sets NO env vars — it only
    swaps images — so this must come from the branch deploy or Terraform.
    """
    cloudbuild = (_REPO_ROOT / "cloudbuild.yaml").read_text()
    assert "TOOLBOX_BQ_PROJECT=" in cloudbuild, (
        "cloudbuild.yaml no longer sets TOOLBOX_BQ_PROJECT on the toolbox container. "
        "Without it every environment silently falls back to tools.yaml's dev default "
        "and bills BigQuery jobs to the wrong project — the exact drift v6.23.0 fixed."
    )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_every_source_is_read_only(path: Path) -> None:
    """Toolbox's ``writeMode`` DEFAULTS to ``allowed`` (arbitrary DML). Every
    source must override it to ``blocked`` — v1 is read-only."""
    for source in _sources(_load_documents(path)):
        assert source.get("writeMode") == "blocked", (
            f"{path}: source {source.get('name')!r} has writeMode={source.get('writeMode')!r}; "
            "Toolbox defaults to 'allowed' (arbitrary INSERT/UPDATE/DROP) so this MUST be set to 'blocked'"
        )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_every_parameter_is_described(path: Path) -> None:
    """Toolbox refuses to boot on a parameter without a description (spike hit
    this). Catch it in CI rather than at container start."""
    for tool in _tools(_load_documents(path)):
        for param in tool.get("parameters", []):
            assert param.get("description"), (
                f"{path}: tool {tool['name']!r} parameter {param.get('name')!r} has no description — Toolbox will refuse to boot"
            )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_every_tool_references_a_declared_source(path: Path) -> None:
    docs = _load_documents(path)
    declared = {s["name"] for s in _sources(docs)}
    for tool in _tools(docs):
        assert tool.get("source") in declared, (
            f"{path}: tool {tool['name']!r} references undeclared source {tool.get('source')!r}"
        )


@pytest.mark.parametrize("path", _shipped_config_paths(), ids=lambda p: p.parent.name)
def test_every_toolset_references_declared_tools(path: Path) -> None:
    docs = _load_documents(path)
    declared = {t["name"] for t in _tools(docs)}
    for toolset in [d for d in docs if d.get("kind") == "toolset"]:
        for name in toolset.get("tools", []):
            assert name in declared, f"{path}: toolset {toolset['name']!r} references undeclared tool {name!r}"
