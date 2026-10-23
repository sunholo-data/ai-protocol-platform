"""`aitana docs` — user document folder management and upload.

Targets:
  GET/POST /api/folders
  GET /api/folders/{folderId}/documents
  POST /api/documents/upload
"""

from __future__ import annotations

import json as _json
import re
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


# Never uploaded from a folder walk: hidden files/dirs and dependency trees.
_SKIP_DIRS = {"node_modules", "__pycache__"}


def _walk(root: Path) -> list[tuple[Path, str]]:
    """Files under ``root`` with their folder path (``root.name`` + subdirs)."""
    out: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file():
            out.append((path, "/".join((root.name, *rel.parent.parts))))
    return out


def _upload_one(client: AIPlatformClient, path: Path, data: dict[str, str]) -> tuple[str, str]:
    """POST one file. Returns (outcome, detail): uploaded | unchanged | unsupported | failed."""
    import httpx

    with path.open("rb") as fh:
        try:
            resp = httpx.post(
                f"{client.base_url}/api/documents/upload",
                headers=client._auth_headers(),
                files={"file": (path.name, fh, _guess_content_type(path))},
                data=data,
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            return "failed", str(exc)
    if resp.status_code == 400 and "not supported" in resp.text:
        return "unsupported", path.suffix or "(no extension)"
    if resp.status_code >= 400:
        return "failed", f"{resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    if body.get("unchanged"):
        return "unchanged", ""
    return "uploaded", f"{body.get('status', '?')}, {body.get('blocksCount', '?')} blocks"


@docs.command("upload")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--folder",
    "folder_id",
    default="",
    show_default=False,
    help="Target folder NAME (e.g. Contracts) or folderId for FILES. A name that matches no folder "
    "is created first. With a directory, a name prefix for the folders it creates.",
)
@click.option("--skill", "skill_id", default="", help="Skill ID to associate the upload with.")
@click.option("--recursive", "-r", is_flag=True, help="Upload directories, keeping their subfolders.")
@click.option(
    "--skip-unchanged/--reupload",
    default=True,
    show_default=True,
    help="Skip files already in the library with identical content.",
)
@click.option("--jobs", "-j", default=3, show_default=True, type=click.IntRange(1, 8), help="Uploads at once.")
@click.pass_context
def upload(
    ctx: click.Context,
    paths: tuple[Path, ...],
    folder_id: str,
    skill_id: str,
    recursive: bool,
    skip_unchanged: bool,
    jobs: int,
) -> None:
    """Upload files, or whole folders with -r, into the document library.

    \b
      aiplatform docs upload reports/*.docx --folder Contracts
      aiplatform docs upload -r ~/Documents/Reports        # → folders Reports, Reports/2026, …

    A directory becomes library folders named after its path (`Reports/2026`).
    Re-running the same folder only uploads what changed. Hidden files and
    node_modules are skipped; unsupported types are counted, not fatal.
    """
    from concurrent.futures import ThreadPoolExecutor

    client = _client(ctx)
    dirs = [p for p in paths if p.is_dir()]
    if dirs and not recursive:
        raise click.UsageError(f"{dirs[0]} is a directory — pass -r/--recursive to upload folders.")

    # A name is what a person has to hand; the uuid is backend addressing
    # (CLAUDE.md #9). Resolved ONCE for plain files — creating the folder when no
    # folder has that name; for directories --folder is a name prefix the
    # backend resolves per file (folder_path), so it is used as-is.
    plain_folder = (
        _resolve_or_create_folder(client, folder_id) if folder_id and any(p.is_file() for p in paths) else folder_id
    )
    common = {"skill_id": skill_id, "skip_unchanged": "true" if skip_unchanged else "false"}
    work: list[tuple[Path, dict[str, str]]] = []
    for p in paths:
        if p.is_file():
            work.append((p, {**common, "folder_id": plain_folder}))
            continue
        for f, folder_path in _walk(p):
            prefix = f"{folder_id.strip('/')}/" if folder_id else ""
            work.append((f, {**common, "folder_path": prefix + folder_path}))

    if not work:
        click.echo("Nothing to upload.")
        return
    click.echo(f"Uploading {len(work)} file(s), {jobs} at a time…")
    counts = {"uploaded": 0, "unchanged": 0, "unsupported": 0, "failed": 0}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_upload_one, client, f, data): (f, data) for f, data in work}
        for fut in futures:
            f, data = futures[fut]
            outcome, detail = fut.result()
            counts[outcome] += 1
            where = data.get("folder_path") or ""
            label = f"{where}/{f.name}" if where else f.name
            click.echo(f"  {outcome:<11} {label}" + (f"  ({detail})" if detail else ""))
            if outcome == "failed":
                failures.append(label)

    click.echo(
        f"Done: {counts['uploaded']} uploaded, {counts['unchanged']} unchanged, "
        f"{counts['unsupported']} unsupported, {counts['failed']} failed."
    )
    if failures:
        raise click.ClickException(f"{len(failures)} file(s) failed: {', '.join(failures[:10])}")


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
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return mapping.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# aitana docs list
# ---------------------------------------------------------------------------


def _items(payload: object, key: str) -> list:
    """Unwrap the API's `{"<key>": [...]}` envelope, tolerating a bare list.

    `/api/folders` returns `_FoldersListResponse` and
    `/api/folders/{id}/documents` returns `_DocumentsListResponse` — objects,
    not arrays. Every reader here used to test `isinstance(payload, list)` and
    fall back to empty, so `docs list` printed `[]` however many documents
    existed and `--folder <name>` never resolved (it 404'd on the literal name).
    Both failures looked like missing data rather than a client that could not
    read the response.
    """
    if isinstance(payload, dict):
        value = payload.get(key)
        return value if isinstance(value, list) else []
    return payload if isinstance(payload, list) else []


def _all_docs(client: AIPlatformClient) -> list:
    result = []
    for f in _items(client.get(_FOLDERS_PATH), "folders"):
        fid = f.get("id") or f.get("folderId", "")
        if not fid:
            continue
        result.extend(_items(client.get(f"/api/folders/{fid}/documents"), "documents"))
    return result


def _resolve_folder(client: AIPlatformClient, folder: str) -> str:
    """Accept a folder NAME or id and return the id (CLAUDE.md #9).

    `--folder Created` is what a person types; the id is backend addressing.
    An unmatched value is passed through untouched so an id still works.
    """
    wanted = (folder or "").strip().casefold()
    if not wanted:
        return folder
    try:
        folders = client.get(_FOLDERS_PATH)
    except Exception:
        return folder
    for entry in _items(folders, "folders"):
        if str(entry.get("name") or "").strip().casefold() == wanted:
            return str(entry.get("id") or folder)
    return folder


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _resolve_or_create_folder(client: AIPlatformClient, folder: str) -> str:
    """`docs upload --folder X` → the id of the caller's folder X, created on miss.

    Accepts a folder id (matched exactly) or a NAME (trimmed, case-insensitive),
    CLAUDE.md #9. A name that matches no folder is created via ``POST
    /api/folders`` and the output says so. Two folders sharing the name is
    refused with both listed — guessing would file the upload somewhere the
    user did not mean.

    This replaced ``_resolve_folder``'s pass-through for uploads: an unknown
    name was sent as the folder id, the backend stored it verbatim, and the
    document was an orphan no folder listed (found 2026-10-15). The backend now
    rejects an unknown id too, so the two sides cannot drift back into that.

    A value shaped like a folder id (a UUID) that matches nothing is an error,
    not a new folder named after a UUID — that is almost always a stale or
    mistyped id.
    """
    wanted = (folder or "").strip()
    folders = _items(client.get(_FOLDERS_PATH), "folders")
    for entry in folders:
        if str(entry.get("id") or "") == wanted:
            return wanted

    key = wanted.casefold()
    matches = [f for f in folders if str(f.get("name") or "").strip().casefold() == key]
    if len(matches) == 1:
        return str(matches[0].get("id"))
    if len(matches) > 1:
        listing = "\n".join(
            f"  {m.get('name')}  (id {m.get('id')}, {m.get('docCount', 0)} document(s))" for m in matches
        )
        raise click.ClickException(
            f"{len(matches)} folders are called '{wanted}':\n{listing}\n"
            "Pass the id of the one you mean with --folder, or rename one first."
        )
    if _UUID_RE.match(wanted):
        raise click.ClickException(
            f"No folder with id '{wanted}'. Run `aiplatform docs folder list` to see your folders, "
            "or pass a folder NAME to create one."
        )

    created = client.post(_FOLDERS_PATH, json={"name": wanted})
    folder_id = str((created or {}).get("id") or "")
    if not folder_id:
        raise click.ClickException(f"Creating folder '{wanted}' returned no id: {created!r}")
    click.echo(f"Created folder '{(created or {}).get('name') or wanted}'.")
    return folder_id


@docs.command("list")
@click.option("--folder", "folder_id", default=None, help="Filter by folder NAME (e.g. Created) or folderId.")
@click.pass_context
def docs_list(ctx: click.Context, folder_id: str | None) -> None:
    """List documents with parse status (all folders or a specific folder)."""
    client = _client(ctx)
    if folder_id:
        payload = client.get(f"/api/folders/{_resolve_folder(client, folder_id)}/documents")
        result = _items(payload, "documents")
    else:
        result = _all_docs(client)
    click.echo(_json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# aitana docs status
# ---------------------------------------------------------------------------


@docs.command("status")
@click.argument("folder_id")
@click.pass_context
def docs_status(ctx: click.Context, folder_id: str) -> None:
    """Show parse progress for a folder (parsed / total). FOLDER_ID may be a name."""
    client = _client(ctx)
    # Takes a NAME as readily as an id, like `docs list --folder` (CLAUDE.md #9)
    # — the id is backend addressing, not what a person has to hand.
    folder_id = _resolve_folder(client, folder_id)
    result = _items(client.get(f"/api/folders/{folder_id}/documents"), "documents")

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


# ---------------------------------------------------------------------------
# aitana docs generate — DOC-CREATE M6
# ---------------------------------------------------------------------------


@docs.command("generate")
@click.option(
    "--file",
    "source_file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Markdown file to convert. Omit to read from stdin.",
)
@click.option("--name", "filename", default="document.docx", show_default=True, help="Output file name.")
@click.option("--target", default="docx", show_default=True, help="Output format (docx, pptx, xlsx, odt, html, md…).")
@click.option(
    "--template",
    default="",
    help='Template document to style the output after, by FILE NAME or title (e.g. "Acme letterhead"). DOCX only.',
)
@click.option("--folder", default="Created", show_default=True, help="Destination folder name.")
@click.pass_context
def docs_generate(
    ctx: click.Context,
    source_file: Path | None,
    filename: str,
    target: str,
    template: str,
    folder: str,
) -> None:
    """Generate a document from Markdown and save it to the workspace.

    The fast inner loop for template work — it exercises the real convert path
    with no chat turn:

      aiplatform docs generate --file brief.md --name offer.docx \
                               --target docx --template "Acme letterhead"

    Reads stdin when --file is omitted:

      echo '# Hello' | aiplatform docs generate --name hello.docx
    """
    import sys

    markdown = source_file.read_text(encoding="utf-8") if source_file else sys.stdin.read()
    if not markdown.strip():
        raise click.ClickException("Nothing to convert: the Markdown source was empty.")

    payload = {
        "contentMarkdown": markdown,
        "filename": filename,
        "target": target,
        "template": template,
        "folder": folder,
    }
    result = _client(ctx).post("/api/documents/generate", json=payload)
    click.echo(_json.dumps(result, indent=2))
