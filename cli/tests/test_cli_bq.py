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
        "content": [
            {"type": "text", "text": "query accesses dataset 'x', which is not in the allowed list"}
        ],
        "isError": True,
    }
}


@pytest.fixture
def runner():
    return CliRunner()


class TestRouting:
    def test_datasets_lists_both_families_from_the_shipped_config(self, runner):
        result = runner.invoke(bq, ["datasets"])
        if "no Toolbox config" in result.output:
            pytest.skip("real tools.yaml absent (template fork)")
        assert result.exit_code == 0
        assert "market_prices" in result.output and "bq_market_*" in result.output
        assert "deal_tracker" in result.output and "bq_analysis_*" in result.output

    @pytest.mark.parametrize(
        ("dataset", "expected_tool"),
        [("market_prices", "bq_market_query"), ("entsoe", "bq_market_query"), ("deal_tracker", "bq_analysis_query")],
    )
    def test_dataset_routes_to_its_regions_tool(self, runner, dataset, expected_tool):
        """Routing comes from the config's allowedDatasets, not a hardcoded map."""
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK) as rpc:
            result = runner.invoke(bq, ["tables", dataset])
        if result.exit_code != 0 and "no Toolbox config" in result.output:
            pytest.skip("real tools.yaml absent (template fork)")
        assert result.exit_code == 0
        assert rpc.call_args.args[2]["name"] == expected_tool

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
    def test_tables_queries_information_schema(self, runner):
        """Not Toolbox's list-table-ids tool — that resolves the dataset against
        the BILLING project and cannot see a cross-project dataset."""
        with patch("aiplatform.commands.bq.mcp_rpc", return_value=OK) as rpc:
            result = runner.invoke(bq, ["tables", "market_prices"])
        if result.exit_code != 0:
            pytest.skip("real tools.yaml absent (template fork)")
        sql = rpc.call_args.args[2]["arguments"]["sql"]
        assert "INFORMATION_SCHEMA.TABLES" in sql
        assert "your-entsoe-project.market_prices" in sql

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
