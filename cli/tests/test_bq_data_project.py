"""The BigQuery data project is deployment identity, not a constant.

Sprint TEMPLATE-INVERT, M3. `bq.py` used to hardcode the customer's BigQuery
project. A fork inheriting it aims every `aiplatform bq` query at a project it
cannot read and gets a permission error that names someone else's
infrastructure — confusing rather than actionable.

The backend suite asserts the literal never returns; this asserts the
replacement actually reads the environment.
"""

from __future__ import annotations

import importlib

import pytest

from aiplatform.commands import bq


@pytest.fixture(autouse=True)
def _reload_after(monkeypatch: pytest.MonkeyPatch):
    yield
    importlib.reload(bq)


def test_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIPLATFORM_BQ_DATA_PROJECT", "acme-warehouse")

    assert bq._data_project() == "acme-warehouse"


def test_unset_returns_a_self_describing_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not an exception: `--help` and dataset listing must still work on a
    fresh checkout. The query then fails with a project name that states its
    own problem rather than a stack trace or someone else's project id.
    """
    monkeypatch.delenv("AIPLATFORM_BQ_DATA_PROJECT", raising=False)

    assert bq._data_project() == "SET-AIPLATFORM_BQ_DATA_PROJECT"


def test_empty_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env var set to "" is a common Cloud Run / CI artefact and must not
    produce a query against the project `` ."""
    monkeypatch.setenv("AIPLATFORM_BQ_DATA_PROJECT", "")

    assert bq._data_project() == "SET-AIPLATFORM_BQ_DATA_PROJECT"


def test_queries_interpolate_the_resolved_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against a half-applied refactor leaving one call site on the old
    constant — the failure mode where `bq tables` works and `bq columns`
    silently queries the wrong project."""
    monkeypatch.setenv("AIPLATFORM_BQ_DATA_PROJECT", "acme-warehouse")
    source = importlib.import_module("aiplatform.commands.bq").__file__ or ""
    text = open(source).read()

    assert "_DATA_PROJECT}" not in text, "a call site still uses the old constant"
    assert text.count("_data_project()") >= 2
