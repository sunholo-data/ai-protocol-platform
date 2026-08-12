"""`aitana docs` — user document folder management and upload.

Targets:
  GET/POST /api/folders
  GET /api/folders/{folderId}/documents
  POST /api/documents/upload
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from aiplatform.http import AIPlatformClient

_FOLDERS_PATH = "/api/folders"


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def docs() -> None:
    """Manage user document folders and uploads."""


# ---------------------------------------------------------------------------
# aitana docs folder <sub>
# ---------------------------------------------------------------------------


@docs.group("folder")
def docs_folder() -> None:
    """Manage document folders (list/new)."""


@docs_folder.command("list")
@click.pass_context
def folder_list(ctx: click.Context) -> None:
    """List all folders with document counts."""
    result = _client(ctx).get(_FOLDERS_PATH)
    click.echo(_json.dumps(result, indent=2))


@docs_folder.command("new")
@click.argument("name")
@click.pass_context
def folder_new(ctx: click.Context, name: str) -> None:
    """Create a new folder and print its folderId."""
    result = _client(ctx).post(_FOLDERS_PATH, json={"name": name})
    click.echo(_json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# aitana docs upload
# ---------------------------------------------------------------------------


@docs.command("upload")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--folder", "folder_id", default="", show_default=False, help="Target folderId (auto-created when absent)."
)
@click.option("--skill", "skill_id", default="", help="Skill ID to associate the upload with.")
@click.pass_context
def upload(ctx: click.Context, files: tuple[Path, ...], folder_id: str, skill_id: str) -> None:
    """Upload one or more files to a folder.

    FILES may be individual paths. Shell globbing works:
      aitana docs upload reports/*.docx --folder <id>
    """
    client = _client(ctx)

    for path in files:
        click.echo(f"Uploading {path.name}…", nl=False)
        url = f"{client.base_url}/api/documents/upload"
        headers = client._auth_headers()

        import httpx

        content_type = _guess_content_type(path)
        with path.open("rb") as fh:
            try:
                resp = httpx.post(
                    url,
                    headers=headers,
                    files={"file": (path.name, fh, content_type)},
                    data={"folder_id": folder_id, "skill_id": skill_id},
                    timeout=120.0,
                )
            except httpx.HTTPError as exc:
                click.echo(f" ERROR: {exc}")
                continue

        if resp.status_code >= 400:
            click.echo(f" FAILED ({resp.status_code}): {resp.text}")
            continue

        data = resp.json()
        status = data.get("status", "?")
        blocks = data.get("blocksCount", "?")
        click.echo(f" {status} ({blocks} blocks)")


# ---------------------------------------------------------------------------
# aitana docs import-from-bucket — DOC-IMPORT-REF M4
# ---------------------------------------------------------------------------


@docs.command("import-from-bucket")
@click.option("--bucket", required=True, help="GCS bucket name (without gs:// prefix).")
@click.option("--object", "object_path", required=True, help="GCS object path within the bucket.")
@click.option("--skill", "skill_id", default="", help="Skill ID to associate the parsed doc with.")
@click.pass_context
def import_from_bucket(ctx: click.Context, bucket: str, object_path: str, skill_id: str) -> None:
    """Parse a GCS-resident document via /api/documents/import-by-reference.

    Reuses the same AILANG Parse pipeline as `aitana docs upload`, but for
    files that already live in GCS — no upload needed. Useful for testing
    the picker / bucket-browser path end-to-end without driving the UI.

    Cache cascade is server-side:
      L2 self-dedup → L4 sentinel-clone → L3 fresh parse.
    Re-running for the same (user, gs://...) returns the existing record.
    """
    client = _client(ctx)
    result = client.post(
        "/api/documents/import-by-reference",
        json={"bucket": bucket, "object": object_path, "skillId": skill_id},
    )
    click.echo(_json.dumps(result, indent=2))


def _guess_content_type(path: Path) -> str:
    ext = path.suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".epub": "application/epub+zip",
    }
    return mapping.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# aitana docs list
# ---------------------------------------------------------------------------


def _all_docs(client: AIPlatformClient) -> list:
    folders = client.get(_FOLDERS_PATH)
    if not isinstance(folders, list):
        return []
    result = []
    for f in folders:
        fid = f.get("id") or f.get("folderId", "")
        if not fid:
            continue
        fdocs = client.get(f"/api/folders/{fid}/documents")
        if isinstance(fdocs, list):
            result.extend(fdocs)
    return result


@docs.command("list")
@click.option("--folder", "folder_id", default=None, help="Filter by folderId.")
@click.pass_context
def docs_list(ctx: click.Context, folder_id: str | None) -> None:
    """List documents with parse status (all folders or a specific folder)."""
    client = _client(ctx)
    result = client.get(f"/api/folders/{folder_id}/documents") if folder_id else _all_docs(client)
    click.echo(_json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# aitana docs status
# ---------------------------------------------------------------------------


@docs.command("status")
@click.argument("folder_id")
@click.pass_context
def docs_status(ctx: click.Context, folder_id: str) -> None:
    """Show parse progress for a folder (parsed / total)."""
    result = _client(ctx).get(f"/api/folders/{folder_id}/documents")
    if not isinstance(result, list):
        click.echo(_json.dumps(result, indent=2))
        return

    total = len(result)
    parsed = sum(1 for d in result if d.get("parseStatus") == "parsed")
    pending = sum(1 for d in result if d.get("parseStatus") in ("pending", "pending_ai_extraction"))
    failed = sum(1 for d in result if d.get("parseStatus") == "failed")

    click.echo(f"Folder: {folder_id}")
    click.echo(f"  Total:   {total}")
    click.echo(f"  Parsed:  {parsed}")
    click.echo(f"  Pending: {pending}")
    click.echo(f"  Failed:  {failed}")
    if total > 0:
        pct = round(parsed / total * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        click.echo(f"  [{bar}] {pct}%")


# ---------------------------------------------------------------------------
# aitana docs corpus <sub>  — RAG corpus management (RAG_DOCUMENTS_ENABLED=true)
# ---------------------------------------------------------------------------

_CORPUS_BASE = "/api/documents/corpus"


@docs.group("corpus")
def docs_corpus() -> None:
    """Manage the user's RAG document corpus (requires RAG_DOCUMENTS_ENABLED=true)."""


@docs_corpus.command("list")
@click.pass_context
def corpus_list(ctx: click.Context) -> None:
    """List all files in the RAG corpus with their resource names."""
    result = _client(ctx).get(f"{_CORPUS_BASE}/files")
    if not isinstance(result, dict):
        click.echo(_json.dumps(result, indent=2))
        return
    corpus_name = result.get("corpus_name", "")
    files = result.get("files", [])
    click.echo(f"Corpus: {corpus_name}")
    click.echo(f"Files ({len(files)}):")
    for f in files:
        name = f.get("name", "")
        display = f.get("display_name", "")
        click.echo(f"  {display or name}")
        if display:
            click.echo(f"    resource: {name}")


@docs_corpus.command("delete")
@click.argument("file_name")
@click.pass_context
def corpus_delete(ctx: click.Context, file_name: str) -> None:
    """Delete a file from the corpus by its resource name."""
    result = _client(ctx).delete(f"{_CORPUS_BASE}/files", params={"file_name": file_name})
    click.echo(_json.dumps(result, indent=2))


@docs_corpus.command("clear")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def corpus_clear(ctx: click.Context, yes: bool) -> None:
    """Delete ALL files from the corpus (corpus itself is retained)."""
    if not yes:
        click.confirm("Delete all files from your RAG corpus?", abort=True)
    result = _client(ctx).get(f"{_CORPUS_BASE}/files")
    files = result.get("files", []) if isinstance(result, dict) else []
    if not files:
        click.echo("Corpus is already empty.")
        return
    for f in files:
        name = f.get("name", "")
        _client(ctx).delete(f"{_CORPUS_BASE}/files", params={"file_name": name})
        click.echo(f"Deleted: {f.get('display_name') or name}")
    click.echo(f"Cleared {len(files)} file(s).")


@docs_corpus.command("search")
@click.argument("query")
@click.option("--top-k", default=5, show_default=True, help="Number of chunks to return.")
@click.pass_context
def corpus_search(ctx: click.Context, query: str, top_k: int) -> None:
    """Ad-hoc semantic search over the corpus (dev/debug)."""
    result = _client(ctx).post(
        f"{_CORPUS_BASE}/search",
        json={"query": query, "top_k": top_k},
    )
    if not isinstance(result, dict):
        click.echo(_json.dumps(result, indent=2))
        return
    chunks = result.get("results", [])
    click.echo(f"Query: {result.get('query', query)}")
    click.echo(f"Results ({len(chunks)}):\n")
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get("source_file", "unknown")
        score = chunk.get("score", 0)
        text = chunk.get("text", "").strip()
        click.echo(f"[{i}] {source} (score: {score:.2f})")
        click.echo(f"    {text[:300]}{'…' if len(text) > 300 else ''}\n")
