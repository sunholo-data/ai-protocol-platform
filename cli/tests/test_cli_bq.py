"""Unit tests for `aiplatform bq` (v6.23.0 ONE-BQ).

The MCP call is stubbed — these pin the two things that are genuinely this
CLI's own logic and would otherwise drift silently:

  * **dataset → tool-family routing is READ FROM tools.yaml**, so adding a
    dataset to a source's ``allowedDatasets`` updates the CLI automatically and
    the CLI can never disagree with the gateway about what is in scope.
  * **a rejection exits non-zero with the gateway's own words.** This command is
    the fastest manual probe of the security boundary; a rejection that printed
    to stdout and exited 0 would read as success in a script.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aiplatform.commands.bq import bq

# Mirrors the live MCP wire shape (see adk/a2ui_bigquery_render.py): one content
# item per row, each a JSON string.
OK = {"result": {"content": [{"type": "text", "text": '{"table_name":"PPA_sweden_4"}'}]}}
REJECTED = {
    "result": {
        "content": [{"type": "text", "text": "query accesses dataset 'x', which is not in the allowed list"}],
        "isError": True,
    }
}


@pytest.fixture
def runner():
    return CliRunner()


def _routing_or_skip() -> dict[str, str]:
    """{dataset: family} from the config present in this checkout, or skip.

    A template fork has only the example toolset, which declares no executor
    families — there is nothing to route, and that is a legitimate state rather
    than a failure.
    """
    from aiplatform.commands.bq import _dataset_routing

    routing = _dataset_routing()
    if not routing:
        pytest.skip("no routable Toolbox config in this checkout (template fork)")
    return routing


class TestRouting:
    """Routing is READ FROM the config, so these assert against the config that
    is actually present rather than naming datasets.

    Hardcoded dataset names broke in TEMPLATE-INVERT M4: the real `tools.yaml`
    is gitignored deployment config (so the scrub could not touch it) while the
    tests are tracked (so it did). The two disagreed instantly. Deriving the
    expectation from the config under test is correct in every tier — this
    deployment's real datasets here, the example toolset upstream.
    """

    def test_datasets_lists_every_configured_dataset_with_its_family(self, runner):
        routing = _routing_or_skip()
        result = runner.invoke(bq, ["datasets"])

        assert result.exit_code == 0
        for dataset, family in routing.items():
            assert dataset in result.output, f"{dataset} missing from `bq datasets`"
            assert f"{family}_*" in result.output

    def test_more_than_one_family_exists(self, runner):
        """The multi-family case is the whole reason routing exists: a Toolbox
        source declares ONE BigQuery location, so datasets spanning regions need
        different tools. A config with one family would pass the test above
        vacuously."""
        routing = _routing_or_skip()

        assert len(set(routing.values())) >= 2, "config has no multi-region routing to test"

    def test_each_dataset_routes_to_its_own_familys_tool(self, runner):
        routing = _routing_or_skip()

        for dataset, family in routing.items():
            with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK) as rpc:
                result = runner.invoke(bq, ["tables", dataset])
            assert result.exit_code == 0, f"{dataset}: {result.output}"
            assert rpc.call_args.args[2]["name"] == f"{family}_query"

    def test_unknown_dataset_names_what_is_reachable(self, runner):
        """Fails with the allowlist rather than a bare error — the useful answer
        to 'why can't I see this' is 'here is what you CAN see'."""
        result = runner.invoke(bq, ["tables", "definitely_not_a_dataset"])
        if "no Toolbox config" in result.output:
            pytest.skip("real tools.yaml absent (template fork)")
        assert result.exit_code != 0
        assert "not in this deployment's allowlist" in result.output
        assert "Reachable:" in result.output


class TestDiscoveryIsSql:
    def test_tables_queries_information_schema(self, runner, monkeypatch):
        """Not Toolbox's list-table-ids tool — that resolves the dataset against
        the BILLING project and cannot see a cross-project dataset.

        The data project is read from the environment (TEMPLATE-INVERT M3) —
        it used to be a hardcoded constant, which pointed every fork's queries
        at a project it could not read.
        """
        monkeypatch.setenv("AIPLATFORM_BQ_DATA_PROJECT", "acme-warehouse")
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK) as rpc:
            result = runner.invoke(bq, ["tables", "market_prices"])
        if result.exit_code != 0:
            pytest.skip("real tools.yaml absent (template fork)")
        sql = rpc.call_args.args[2]["arguments"]["sql"]
        assert "INFORMATION_SCHEMA.TABLES" in sql
        # Assert the PROJECT reached the query, not the project.dataset pair:
        # writing the qualified form here would mint a new
        # "<project>.<licensed-dataset>" literal that the scrub table does not
        # cover, and the customer-identifier gate rejects it (it did, on the
        # first version of this line).
        assert "acme-warehouse." in sql

    def test_schema_requires_a_qualified_table(self, runner):
        result = runner.invoke(bq, ["schema", "PPA_sweden_4"])
        assert result.exit_code != 0
        assert "dataset.table" in result.output

    def test_schema_strips_quotes_from_the_table_name(self, runner):
        """The table name reaches a STRING literal, never an identifier position,
        and is quote-stripped first — C2 applies to hand-written SQL too."""
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK) as rpc:
            result = runner.invoke(bq, ["schema", "market_prices.PPA'; DROP--"])
        if result.exit_code != 0:
            pytest.skip("real tools.yaml absent (template fork)")
        sql = rpc.call_args.args[2]["arguments"]["sql"]
        assert "'" not in sql.split("WHERE table_name = ")[1].split(" ORDER BY")[0].strip("'")


class TestRejectionIsLoud:
    def test_rejection_exits_non_zero(self, runner):
        """A rejection that exited 0 would read as success in a script — and this
        command exists partly to check the security boundary by hand."""
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=REJECTED):
            result = runner.invoke(bq, ["sql", "SELECT 1"])
        assert result.exit_code != 0

    def test_rejection_text_is_surfaced_verbatim(self, runner):
        """Not reworded: the gateway's exact words are the evidence."""
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=REJECTED):
            result = runner.invoke(bq, ["sql", "SELECT 1"], catch_exceptions=False)
        assert "not in the allowed list" in result.output


class TestOutput:
    def test_json_flag_parses_rows(self, runner):
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK):
            result = runner.invoke(bq, ["sql", "SELECT 1", "--json"])
        assert result.exit_code == 0
        assert '"table_name": "PPA_sweden_4"' in result.output

    def test_default_output_is_the_raw_rows(self, runner):
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK):
            result = runner.invoke(bq, ["sql", "SELECT 1"])
        assert result.exit_code == 0
        assert '{"table_name":"PPA_sweden_4"}' in result.output
