"""The project floor: in a project chat, the agent reads only the project's documents.

Decided 2026-10-20 (Mark: *"the agent should only look inside the project -
strict"*) after a live check: asked about a document that had just been taken out
of the project, the agent came up empty in ``search_project_documents``, listed
the whole library and answered from it anyway. Prompting alone does not stop a
determined model, so this is enforced at the tool boundary, in one place, for
every skill (design: docs/design/private/projects.md §6.3).

ONLY in a chat scoped to a project the caller owns (:func:`current_project`):

* **Tools that search or list documents outside the project are refused**:
  the user's whole RAG corpus, bucket listings, the shared-library enterprise
  search sub-agent, the workshop docs, the org-bucket readers. Web search, maps,
  data/BigQuery, code, memory, the project tools and ``search_project_documents``
  stay allowed: the web is not the user's documents.
* **Every call naming a document is checked**: each id in ``doc_id``-style
  arguments (and ``gs://`` URLs, and ``doc:<id>.json`` artifact names) must be a
  document in the project. Exception: documents the USER attached to this chat as
  tabs (state ``document_ids``, written from ``forwardedProps.document_ids``),
  because the user chose them explicitly. Ids the agent merely read before
  (``chat_sessions.documentIds``) are not an exception — that would be a way out.
* A refusal is a normal tool result phrased for the model — say so, offer
  ``add_to_project``, look nowhere else — never an exception, and it names the
  document by filename, never by raw id (#9).

Unscoped chats: the callback returns None before reading anything.

Synchronous on purpose: ``compose_before_tool_callbacks`` does not await. The
reads are single Firestore gets (the project check is cached in project_scope).
"""

from __future__ import annotations

import logging
from typing import Any

from adk.project_scope import current_project
from extensions import outside_project_tools

log = logging.getLogger(__name__)

_PARSED_DOCS = "parsed_documents"

# Tools that reach documents OUTSIDE the project. Name-gated, so a tool added
# later that searches the wider library must be listed here (see the inventory
# test in tests/unit/test_project_floor.py).
OUTSIDE_DOCUMENT_TOOLS: frozenset[str] = frozenset(
    {
        "search_documents",  # tools/rag_tool.py — the user's whole RAG corpus
        "list_bucket_documents",  # adk/tools.py — any gs:// bucket listing
        "search_workshop_docs",  # tools/workshop_docs.py — workshop corpus
        "enterprise_search_agent",  # ai_search AgentTool — the shared library
        "list_org_documents",  # tools/org_documents.py — the bound org bucket
        "read_org_document",
    }
    | outside_project_tools()
)

# Arguments that carry one document identity / several / a gs:// source.
_DOC_ID_ARGS = ("doc_id", "document_id", "left_doc_id", "right_doc_id", "ref")
_DOC_LIST_ARGS = ("doc_ids", "document_ids")
_GS_URL_ARGS = ("gs_url", "left_gs_url", "right_gs_url")
_ARTIFACT_ARGS = ("artifact_id", "artifact_names")


def _normalise_doc_id(value: str) -> str:
    v = value.strip()
    if v.lower().startswith("[ref:") and v.endswith("]"):
        v = v[5:-1].strip()
    return v.removeprefix("doc:").removesuffix(".json")


def _named_doc_ids(tool_name: str, args: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(doc ids, gs urls)`` this call names. Artifact names count only when
    they are document artifacts (``doc:<id>.json``)."""
    ids: list[str] = []
    urls: list[str] = []
    for key in _DOC_ID_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            ids.append(_normalise_doc_id(value))
    for key in _DOC_LIST_ARGS:
        value = args.get(key)
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            ids += [_normalise_doc_id(v) for v in value if isinstance(v, str) and v.strip()]
    for key in _GS_URL_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            urls.append(value.strip())
    for key in _ARTIFACT_ARGS:
        value = args.get(key)
        names = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        ids += [_normalise_doc_id(n) for n in names if isinstance(n, str) and n.strip().startswith("doc:")]
    return [i for i in dict.fromkeys(ids) if i], list(dict.fromkeys(urls))


def _user_attached(tool_context: Any) -> set[str]:
    try:
        return {str(d) for d in (tool_context.state.get("document_ids") or []) if d}
    except Exception:
        return set()


def _get_doc(doc_id: str) -> dict[str, Any] | None:
    from db.firestore import get_document

    try:
        return get_document(_PARSED_DOCS, doc_id)
    except Exception as exc:  # unreadable → treat as unknown, the tool itself will fail
        log.warning("project floor: could not read document %s: %s", doc_id, exc)
        return None


def _doc_by_source_url(uid: str, url: str) -> tuple[str, dict[str, Any]] | None:
    from db.firestore import query_documents

    try:
        rows = query_documents(_PARSED_DOCS, filters=[("userId", "==", uid), ("sourceUrl", "==", url)], limit=1)
    except Exception as exc:
        log.warning("project floor: source lookup failed for %s: %s", url, exc)
        return None
    for row in rows or []:
        doc_id = row.get("id") or row.get("__id") or ""
        return str(doc_id), row
    return None


def _refusal(tool_name: str, project: dict[str, Any], what: str) -> dict[str, Any]:
    name = project.get("name") or "this project"
    return {
        "status": "outside_project",
        "tool": tool_name,
        "message": (
            f"{what} isn't in the project '{name}'. This chat can only use that project's documents. "
            "Say so plainly and ASK the user whether to add it to the project. Do not add it yourself "
            "(add_to_project only after the user says yes in a later message), and do not look elsewhere "
            "for the answer."
        ),
    }


def make_project_floor_callback() -> Any:
    """A ``before_tool_callback`` enforcing the project floor (see module doc)."""

    def _floor(tool: Any, args: dict, tool_context: Any) -> dict[str, Any] | None:
        tool_name = getattr(tool, "name", "") or ""
        if tool_name == "transfer_to_agent":
            return None
        try:
            project = current_project(tool_context)
        except Exception:
            log.exception("project floor: could not resolve the chat's project; not enforcing")
            return None
        if project is None:
            return None  # unscoped chat: unchanged

        if tool_name in OUTSIDE_DOCUMENT_TOOLS:
            log.info("project floor: refused %s in project %s", tool_name, project.get("id"))
            return _refusal(tool_name, project, "That search reaches documents that")

        ids, urls = _named_doc_ids(tool_name, args or {})
        if not ids and not urls:
            return None
        uid = _caller_uid(tool_context)
        project_id = str(project.get("id") or "")
        attached = _user_attached(tool_context)
        for doc_id in ids:
            if doc_id in attached:
                continue
            doc = _get_doc(doc_id)
            if doc is None:
                continue  # unknown id: the tool reports "not found" itself
            if doc.get("userId") == uid and project_id in (doc.get("projectIds") or []):
                continue
            filename = doc.get("originalFilename") if doc.get("userId") == uid else None
            log.info("project floor: refused %s on a document outside project %s", tool_name, project_id)
            return _refusal(tool_name, project, f"“{filename}”" if filename else "That document")
        for url in urls:
            hit = _doc_by_source_url(uid, url)
            if hit is not None:
                doc_id, doc = hit
                if doc_id in attached or project_id in (doc.get("projectIds") or []):
                    continue
                return _refusal(tool_name, project, f"“{doc.get('originalFilename') or 'That document'}”")
            log.info("project floor: refused %s on a source outside project %s", tool_name, project_id)
            return _refusal(tool_name, project, "That document")
        return None

    return _floor


def _caller_uid(ctx: Any) -> str:
    from tools.documents.context import resolve_caller_uid

    return resolve_caller_uid(ctx)


__all__ = ["OUTSIDE_DOCUMENT_TOOLS", "make_project_floor_callback"]
