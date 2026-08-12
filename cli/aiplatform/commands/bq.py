"""`aiplatform bq` — query ONE's scoped BigQuery warehouse from a terminal.

The task-shaped counterpart to ``aiplatform toolbox probe``. Probe speaks MCP
("call tool X with these JSON args against this URL"); this group speaks
BigQuery ("what tables are in market_prices?"), which is what you actually want when
reproducing a data question or checking the security boundary by hand.

Design: docs/design/v6.23.0/one-bigquery.md

## Why `bq sql`, not `bq query`

``generated-document-outputs.md`` Track C reserves ``aiplatform bq query list``
for Dana's NAMED query library. If ad-hoc SQL took the ``query`` verb, Click
would read ``bq query list`` as "run the SQL `list`". Keeping ad-hoc on ``sql``
leaves the ``query`` namespace free for that library to land in cleanly.

## Dataset → tool routing comes from tools.yaml, not a constant

ONE's datasets span two BigQuery regions, so the toolset carries two parallel
tool families (``bq_market_*`` / ``bq_analysis_*``). Which one serves a dataset
is derived by READING the shipped ``tools.yaml`` sources, so this CLI cannot
drift from the gateway: add a dataset to a source's ``allowedDatasets`` and the
routing follows automatically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from aiplatform.commands.toolbox import mcp_rpc

# parents: [0]=commands [1]=aiplatform [2]=cli [3]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLBOX_DIR = _REPO_ROOT / "infrastructure" / "mcp-toolbox"

# The sidecar is loopback in every environment by design (v6.14.0) — there is no
# per-env URL to pass. Override only for a non-default port in local dev.
DEFAULT_URL = "http://127.0.0.1:5000/mcp/one-bigquery"

# The DATA project (not the billing project the source runs jobs in).
_DATA_PROJECT = "your-entsoe-project"

# source name -> tool-family prefix. The two families exist because a Toolbox
# source declares ONE BigQuery location and ONE's datasets span two.
_FAMILY_BY_SOURCE = {"one-curated-ew4": "bq_market", "one-curated-ew1": "bq_analysis"}


def _config_path() -> Path | None:
    for name in ("tools.yaml", "tools.example.yaml"):
        p = _TOOLBOX_DIR / name
        if p.exists():
            return p
    return None


def _dataset_routing() -> dict[str, str]:
    """``{bare dataset name: tool-family prefix}`` read from the shipped config.

    Keyed on the BARE name (``market_prices``) rather than the qualified one so the CLI
    accepts what a human types. Returns empty when the config is absent (template
    fork) — callers degrade to a clear error rather than a traceback.
    """
    path = _config_path()
    if path is None:
        return {}
    routing: dict[str, str] = {}
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "source":
            continue
        family = _FAMILY_BY_SOURCE.get(doc.get("name", ""))
        if not family:
            continue
        for qualified in doc.get("allowedDatasets") or []:
            routing[str(qualified).split(".")[-1]] = family
    return routing


def _family_for(dataset: str) -> str:
    routing = _dataset_routing()
    if not routing:
        raise click.ClickException(
            f"no Toolbox config found under {_TOOLBOX_DIR} — cannot route a dataset to a tool family"
        )
    family = routing.get(dataset)
    if family is None:
        raise click.ClickException(
            f"dataset {dataset!r} is not in this deployment's allowlist. "
            f"Reachable: {', '.join(sorted(routing)) or '(none)'}"
        )
    return family


def _call(url: str, tool: str, arguments: dict) -> tuple[list[dict], bool]:
    """Call one tool. Returns ``(content items, is_error)``."""
    payload = mcp_rpc(url, "tools/call", {"name": tool, "arguments": arguments})
    result = payload.get("result") or {}
    return (result.get("content") or []), bool(result.get("isError"))


def _texts(content: list[dict]) -> list[str]:
    return [str(i.get("text", "")) for i in content if isinstance(i, dict)]


def _emit(content: list[dict], is_error: bool, *, as_json: bool) -> None:
    """Print a tool result, or fail loudly with the gateway's own message.

    A rejection is surfaced verbatim (not reworded) — when checking a security
    boundary by hand, the exact text is the evidence.
    """
    texts = _texts(content)
    if is_error:
        for line in texts:
            click.echo(click.style(line, fg="red"), err=True)
        raise SystemExit(1)
    if as_json:
        rows = []
        for text in texts:
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError:
                rows.append(text)
        click.echo(json.dumps(rows, indent=2))
        return
    for line in texts:
        click.echo(line)


_url_option = click.option("--url", default=DEFAULT_URL, show_default=True, help="Toolset-scoped MCP endpoint.")
_json_option = click.option("--json", "as_json", is_flag=True, help="Emit parsed JSON instead of raw rows.")


@click.group()
def bq() -> None:
    """Query the scoped BigQuery warehouse through the Toolbox sidecar."""


@bq.command("datasets")
def datasets() -> None:
    """List the datasets this deployment can reach, and their tool family.

    Read from the shipped ``tools.yaml`` rather than from BigQuery: the answer
    wanted here is "what is in scope", which is a property of the config, not of
    the warehouse. A dataset that exists but is not allowlisted is correctly
    absent.
    """
    routing = _dataset_routing()
    if not routing:
        raise click.ClickException(f"no Toolbox config found under {_TOOLBOX_DIR}")
    width = max(len(d) for d in routing)
    for dataset, family in sorted(routing.items()):
        click.echo(f"  {dataset.ljust(width)}  → {family}_*")


@bq.command("tables")
@click.argument("dataset")
@_url_option
@_json_option
def tables(dataset: str, url: str, as_json: bool) -> None:
    """List tables in DATASET (e.g. `aiplatform bq tables market_prices`).

    Runs INFORMATION_SCHEMA through the query tool rather than Toolbox's
    dedicated list-tables tool — see the ONLY-the-executor note in tools.yaml:
    that tool resolves the dataset against the BILLING project and so cannot see
    a cross-project dataset.
    """
    family = _family_for(dataset)
    sql_text = (
        f"SELECT table_name FROM `{_DATA_PROJECT}.{dataset}.INFORMATION_SCHEMA.TABLES` ORDER BY table_name"
    )
    content, is_error = _call(url, f"{family}_query", {"sql": sql_text})
    _emit(content, is_error, as_json=as_json)


@bq.command("schema")
@click.argument("table")
@_url_option
@_json_option
def schema(table: str, url: str, as_json: bool) -> None:
    """Show the schema of TABLE, given as `dataset.table`."""
    if "." not in table:
        raise click.ClickException("TABLE must be `dataset.table`, e.g. market_prices.PPA_sweden_4")
    dataset, _, table_name = table.rpartition(".")
    dataset = dataset.split(".")[-1]  # tolerate a fully-qualified project.dataset.table
    family = _family_for(dataset)
    # table_name reaches a STRING literal, never an identifier position, and it is
    # quote-stripped first — the C2 rule applies to hand-written SQL too.
    safe = table_name.replace("\\", "").replace("'", "").replace('"', "")
    sql_text = (
        f"SELECT column_name, data_type FROM `{_DATA_PROJECT}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
        f"WHERE table_name = '{safe}' ORDER BY ordinal_position"
    )
    content, is_error = _call(url, f"{family}_query", {"sql": sql_text})
    _emit(content, is_error, as_json=as_json)


@bq.command("sql")
@click.argument("statement")
@click.option(
    "--family",
    type=click.Choice(["market", "analysis"]),
    default="market",
    show_default=True,
    help="Which region's source to run against. Market = market_prices/entsoe (europe-west4).",
)
@_url_option
@_json_option
def sql(statement: str, family: str, url: str, as_json: bool) -> None:
    """Run a read-only SQL STATEMENT.

    Doubles as the fastest manual probe of the security boundary — an
    out-of-allowlist query prints the gateway's own rejection and exits 1:

        aiplatform bq sql "SELECT 1 FROM \\`proj.not_allowed.t\\`"
    """
    content, is_error = _call(url, f"bq_{family}_query", {"sql": statement})
    _emit(content, is_error, as_json=as_json)


if __name__ == "__main__":  # pragma: no cover
    bq()
    sys.exit(0)
