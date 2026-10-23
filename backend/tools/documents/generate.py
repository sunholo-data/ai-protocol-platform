"""Document generation — a brief becomes a document in the workspace.

Generation is **not a new output channel**; it is a second way to put a document
into the workspace. An uploaded document and a generated one are indistinguishable
to everything downstream — context building, RAG, comparison, the browser,
artifacts — because both go through the same `ingest_document()`.

That is deliberate, and it is what makes the loop work. Earlier designs ended at
a download link, which is a dead end: it cannot be read back as context for the
next turn, compared against another document, regenerated with a change, or found
tomorrow in the file browser. This feature exists to enable *generate, look,
adjust, generate again*; a download breaks that on the first iteration.

The split of labour (axiom 4):

    model authors CommonMark  →  ailang-parse converts  →  ingest_document()

The model is never asked to emit binary Office XML.

See docs/design/v6.32.0/document-creation.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

import db.folders as folders_db
from tools.documents.ailang_parse import ConvertOutcome, convert_document_file, convert_markdown
from tools.documents.ingest import ingest_document

log = logging.getLogger(__name__)

# Generated documents land here. A system folder, so `ensure_default_folder`
# never picks it for an upload that named no folder (see db/folders.py).
CREATED_FOLDER_NAME = "Created"

_COLLECTION = "parsed_documents"


# --- Template resolution (CLAUDE.md #9 — friendly names, not raw IDs) --------
#
# A template is a workspace document like any other, referenced by NAME. The
# model never holds a UUID: `template="Acme letterhead"` resolves here, and only
# here, to a doc-id. Aliases accepted: exact doc-id, original filename, filename
# without extension, and title — all case-insensitively. That direction is the
# only safe one; we resolve friendly→id and never the reverse.


# Words people append when naming a template that carry no identity: "use our
# house-style TEMPLATE", "the letterhead DOC". The model passes back what the
# user said, so the matcher has to tolerate them (CLAUDE.md #9 — accept aliases
# on input; a tool that demands the exact string is the recurring bug).
# NOT "style" or "format": those are load-bearing in real template names
# ("house-style"), and stripping them collapsed house-style onto anything
# called "house". Only structurally meaningless words belong here.
_TEMPLATE_NOISE_WORDS = ("template", "templates", "document", "doc", "docx", "file")


def _normalise_template_name(value: str) -> str:
    """Fold a spoken template name to a comparable key.

    Case, separators and trailing noise words are all things a person varies
    without meaning a different document: "House Style", "house_style" and
    "the house-style template" are one template. Deliberately NOT a substring
    or fuzzy match — those would happily resolve "offer" to "offer-v2-old" and
    style a document from the wrong file.
    """
    text = re.sub(r"[\s_\-]+", " ", (value or "").strip().casefold()).strip()
    if not text:
        return ""
    # Drop a file extension the speaker included ("letterhead.docx").
    text = re.sub(r"\.(docx|doc|dotx)$", "", text).strip()
    # Peel leading articles and trailing noise, repeatedly: "the letterhead doc".
    changed = True
    while changed and text:
        changed = False
        for article in ("the ", "our ", "a ", "my "):
            if text.startswith(article):
                text = text[len(article) :].strip()
                changed = True
        for noise in _TEMPLATE_NOISE_WORDS:
            if text.endswith(" " + noise):
                text = text[: -len(noise) - 1].strip()
                changed = True
    return re.sub(r"\s+", "", text)


def _template_aliases(doc: dict[str, Any]) -> set[str]:
    filename = str(doc.get("originalFilename") or "")
    raw = {
        str(doc.get("__id") or ""),
        filename,
        PurePosixPath(filename).stem,
        str(doc.get("title") or ""),
    }
    aliases = {a.strip().casefold() for a in raw if a and a.strip()}
    aliases |= {_normalise_template_name(a) for a in raw if a and a.strip()}
    return {a for a in aliases if a}


def _find_brand_template(domain: str, wanted: str) -> dict[str, Any] | None:
    """Resolve a template against the TENANT's shared brand templates.

    A workspace document belongs to one user, so a letterhead uploaded by one
    person is invisible to everyone else in the same company — each of them
    would have to upload their own copy and keep it in step. The tenant's brand
    templates are shared, so a house style is configured once per environment
    and every colleague gets it.

    Returns a record shaped like a `parsed_documents` row (the fields the
    caller reads: `__id`, `originalFilename`, `sourceUrl`) so the two lookups
    are interchangeable at the call site.
    """
    from tools.documents.brand_assets import gs_url_for, list_brand_assets

    if not domain or not wanted:
        return None

    assets = list_brand_assets(domain, "templates")
    if not assets.ok:
        # Not an error worth failing the generation for: the tenant simply may
        # not have brand templates. Logged so a misconfigured env is findable.
        log.info("brand template lookup for %s: %s", domain, assets.error)
        return None

    normalised_wanted = _normalise_template_name(wanted)
    for name in assets.names:
        aliases = {
            name.casefold(),
            PurePosixPath(name).stem.casefold(),
            _normalise_template_name(name),
        }
        if wanted in aliases or (normalised_wanted and normalised_wanted in aliases):
            return {
                "__id": f"brand:{domain}/templates/{name}",
                "originalFilename": name,
                "sourceUrl": gs_url_for(assets.bucket, domain, "templates", name),
            }
    return None


# Names that mean "no template", so a caller can opt OUT of the house style.
# Spoken words rather than a magic flag: the model is told to pass what the user
# said, and "plain" is what they say (CLAUDE.md #9 — friendly names on input).
_NO_TEMPLATE = frozenset({"none", "plain", "no template", "no styling", "default"})

# A tenant that has exactly one brand template means that one. A tenant with
# several is ambiguous, so a file whose stem is `house-style` settles it — and
# a tenant with several and no such file gets NO default rather than a guess,
# because silently picking one of three letterheads is worse than asking.
_HOUSE_STYLE_STEM = "house-style"


def _tenant_house_style(domain: str) -> dict[str, Any] | None:
    """The tenant's default template, or None if they have no unambiguous one."""
    from tools.documents.brand_assets import gs_url_for, list_brand_assets

    if not domain:
        return None
    assets = list_brand_assets(domain, "templates")
    if not assets.ok or not assets.names:
        return None

    chosen = ""
    for name in assets.names:
        if PurePosixPath(name).stem.casefold() == _HOUSE_STYLE_STEM:
            chosen = name
            break
    if not chosen:
        if len(assets.names) != 1:
            log.info(
                "no house style for %s: %d templates and none named %r",
                domain,
                len(assets.names),
                _HOUSE_STYLE_STEM,
            )
            return None
        chosen = assets.names[0]

    return {
        "__id": f"brand:{domain}/templates/{chosen}",
        "originalFilename": chosen,
        "sourceUrl": gs_url_for(assets.bucket, domain, "templates", chosen),
    }


def _find_template(user_id: str, template: str, domain: str = "") -> dict[str, Any] | None:
    """Resolve a template reference to its parsed_documents record, or None.

    The caller's OWN workspace wins over the tenant's shared brand templates:
    someone who has uploaded their own "letterhead" means that one, and a
    shared default should never silently override a document a user put there
    themselves.
    """
    from db.firestore import query_documents

    wanted = (template or "").strip().casefold()
    if not wanted:
        return None

    docs = query_documents(_COLLECTION, filters=[("userId", "==", user_id)], limit=500)
    for doc in docs:
        if wanted in _template_aliases(doc):
            return doc

    return _find_brand_template(domain, wanted)


# Why a resolved template can be absent. Logged on every path so that "this
# document came out unstyled" is answerable from the logs rather than only from
# the bytes — the gap that made the 2026-09-09 regression invisible.
_NO_TEMPLATE_REASONS = frozenset({"opted-out", "no-house-style", "unstylable-target", "not-found"})


def _choose_template(
    *,
    uid: str,
    domain: str,
    wanted: str,
    target: str,
    exclude_doc_id: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """The template for the tenant generation, resolved the same way on every path.

    Generation, revision and restyle all style a document, so they must agree on
    what "no template named" means. They did not: the house-style default and the
    tenant lookup were wired into the tool path only, so a document generated
    on-brand lost its styling on the first edit and could not get it back
    (2026-09-09, `Poem - Power Purchase Agreements.docx`). One function, three
    callers — the same rule the ingest extraction exists to enforce.

    `exclude_doc_id` is the document being revised. A document must never be its
    own reference doc: the converter accepts it, reports
    `referenceDocApplied: True`, carries **zero** parts, and overwrites the
    styling with the unstyled bytes it just read. That is a silent destructive
    no-op, so it falls back to the default instead of being honoured.

    Returns `(record, reason)`. A `None` record with reason `not-found` is the
    only one a caller should turn into an error; the rest mean "no template", and
    the caller generates unstyled.
    """
    wanted = (wanted or "").strip()

    if wanted.casefold() in _NO_TEMPLATE:
        log.info("template for %s: none (opted out)", domain or "<no domain>")
        return None, "opted-out"

    if not wanted:
        # Only DOCX carries a styling container; defaulting on any other target
        # applies a template the converter then refuses.
        if target != "docx":
            return None, "unstylable-target"
        house = _tenant_house_style(domain)
        if house is None:
            log.info("template for %s: no house style available", domain or "<no domain>")
            return None, "no-house-style"
        log.info("template for %s: house style %s", domain, house.get("originalFilename"))
        return house, "house-style"

    record = _find_template(uid, wanted, domain)
    if record is None:
        log.info("template %r for %s: not found in workspace or brand templates", wanted, domain or "<no domain>")
        return None, "not-found"

    resolved_id = str(record.get("__id") or "")
    if exclude_doc_id and resolved_id == exclude_doc_id:
        log.warning(
            "template %r resolved to the document being revised (%s); using the default instead",
            wanted,
            exclude_doc_id,
        )
        return _choose_template(uid=uid, domain=domain, wanted="", target=target)

    log.info("template %r for %s: resolved to %s", wanted, domain or "<no domain>", resolved_id)
    return record, "named"


def _download_to_tempfile(gs_url: str, suffix: str = ".docx") -> str | None:
    """Fetch a template's bytes to a local path for upload to the converter.

    Passed by upload from our backend rather than by `gcsRef` on purpose: gcsRef
    would require granting the docparse service account read access on a
    customer's tagged bucket. That cross-org grant is manual, recurring per
    environment, and widens who can read the customer's bucket — the pattern
    that has already bitten this deployment. Our backend already has the read.
    """
    from google.cloud import storage as gcs

    try:
        parts = urlparse(gs_url)
        tmp_dir = tempfile.mkdtemp(prefix="tmpl-")
        path = os.path.join(tmp_dir, f"template{suffix}")
        gcs.Client().bucket(parts.netloc).blob(parts.path.lstrip("/")).download_to_filename(path)
        return path
    except Exception as exc:
        log.error("template download failed for %s: %s", gs_url, exc)
        return None


def _progress(label: str) -> None:
    """Visible working state — a multi-second generation must never be silent
    (principle #8). Fail-open: progress is never worth failing a generation for."""
    try:
        from tools.resilient_genai import emit_stage_progress

        emit_stage_progress(label)
    except Exception as exc:  # pragma: no cover — cosmetic path
        log.debug("generate_document: progress emit suppressed: %s", exc)


def _ensure_extension(filename: str, target: str) -> str:
    name = (filename or "document").strip().replace("/", "_").replace("\\", "_")
    if PurePosixPath(name).suffix.lower() == f".{target}":
        return name
    return f"{PurePosixPath(name).stem or 'document'}.{target}"


async def generate_document(
    content_markdown: str,
    filename: str = "document.docx",
    target: str = "docx",
    template: str = "",
    tool_context: Any = None,
) -> dict[str, Any]:
    """Create a document from markdown and save it to the user's workspace.

    Write the document body as Markdown — headings, paragraphs, bullet lists,
    tables and links all carry through. The file is converted to a real Office
    document and saved to the user's "Created" folder, where it appears in their
    file browser and can be read back, compared or regenerated like any upload.

    Args:
        content_markdown: The document content, as Markdown.
        filename: Desired file name, e.g. "Offer - Northwind.docx".
        target: Output format. "docx" (default) is the only one that can be
            styled from a template.
        template: Template document to style the output after, given by its
            FILE NAME or title as the user would say it (e.g. "Acme
            letterhead"). Its styles, theme, fonts, headers and footers are
            applied to the result.

            LEAVE THIS EMPTY unless the user named a specific template. Empty
            does not mean unstyled: it applies the organisation's own house
            style automatically, which is almost always what someone wants — a
            document that looks like it came from their company. The result's
            `templateUsed` tells you which template was applied, so say that
            back to the user rather than guessing.

            Pass "plain" only if the user explicitly asks for no styling.

    Returns:
        A dict describing the created document: docId, filename, folder, target,
        templateUsed, blockCount and status. On failure, status is "failed" with
        a human-readable error and the markdown source, so nothing is lost.
    """
    from auth.firebase_auth import resolve_user_by_uid
    from tools.documents.context import resolve_caller_uid

    target = (target or "docx").lower().lstrip(".")
    out_name = _ensure_extension(filename, target)

    uid = resolve_caller_uid(tool_context)
    if not uid:
        return {
            "status": "failed",
            "error": "Could not identify the signed-in user, so the document was not saved.",
            "filename": out_name,
            "sourceMarkdown": content_markdown,
        }

    user = resolve_user_by_uid(uid)
    if user is None:
        return {
            "status": "failed",
            "error": "Your account could not be verified, so the document was not saved.",
            "filename": out_name,
            "sourceMarkdown": content_markdown,
        }

    # --- Resolve the template ----------------------------------------------
    # A named template is looked up; NOTHING named falls back to the tenant's
    # house style, so a document is on-brand by default rather than only when
    # someone remembers to ask. `templateUsed` in the result names whichever was
    # applied, so the default is visible and never silent (#8). To opt out, name
    # one of _NO_TEMPLATE.
    template_path = ""
    template_doc_id = ""
    template_label = ""
    domain = getattr(user, "domain", "") or ""
    wanted = template.strip()

    if wanted:
        _progress(f"Looking up template “{wanted}”…")
    record, reason = await asyncio.to_thread(_choose_template, uid=uid, domain=domain, wanted=wanted, target=target)
    if reason == "not-found":
        return {
            "status": "failed",
            "error": (
                f"No document called “{wanted}” is in your workspace, and your "
                "organisation has no shared template by that name either, so it could not be "
                "used. Upload the template first, or generate without one."
            ),
            "filename": out_name,
            "sourceMarkdown": content_markdown,
        }

    if record is not None:
        if reason == "house-style":
            _progress(f"Applying your organisation's house style ({record['originalFilename']})…")
        template_doc_id = str(record.get("__id") or "")
        template_label = str(record.get("originalFilename") or wanted)
        gs_url = str(record.get("sourceUrl") or "")
        template_path = await asyncio.to_thread(_download_to_tempfile, gs_url) or ""
        if not template_path and reason == "house-style":
            # Fail SOFT: an unreadable house style must not block a document the
            # user did not ask to be styled. Logged, and the result's empty
            # templateUsed shows it was not applied.
            log.warning("house style for %s could not be read; generating unstyled", domain)
            template_doc_id = ""
            template_label = ""
        elif not template_path:
            return {
                "status": "failed",
                "error": f"The template “{template_label}” could not be read from storage.",
                "filename": out_name,
                "sourceMarkdown": content_markdown,
            }

    try:
        # --- Convert --------------------------------------------------------
        _progress("Converting to a document…")
        outcome = await convert_markdown(
            content_markdown,
            target=target,
            filename=out_name,
            reference_doc_path=template_path,
        )
        if not outcome.ok:
            # Never an empty success: hand back the source plus the reason.
            return {
                "status": "failed",
                "error": outcome.error or "Conversion failed.",
                "errorCode": outcome.error_code,
                "filename": out_name,
                "templateUsed": template_label,
                "sourceMarkdown": content_markdown,
            }

        # --- Put it in the workspace, through the SAME door as an upload ----
        _progress("Saving to your workspace…")
        folder_id = await asyncio.to_thread(folders_db.ensure_named_folder, uid, CREATED_FOLDER_NAME, True)

        # The same provenance the HTTP paths record, plus where the call came
        # from. It used to be a hand-built dict carrying only the doc-id, so a
        # document generated by the model — the path users actually hit — showed
        # a styled document with no template NAME (useDocument.ts compensates for
        # exactly this), and the "applied but carried 0 parts" warning could
        # never fire on it.
        provenance = {
            **_template_provenance(template_doc_id, template_label, target, "tool", outcome),
            "skillId": _skill_id(tool_context),
            "sessionId": _session_id(tool_context),
        }

        try:
            saved = await ingest_document(
                user=user,
                file_bytes=outcome.content or b"",
                filename=out_name,
                content_type=outcome.content_type,
                folder_id=folder_id,
                skill_id=provenance["skillId"],
                source="generated",
                generated_from=provenance,
                # v6.34.0 P3: the markdown IS the editable source. There is no
                # write path into a .docx, so a later revision regenerates from
                # this rather than mutating the binary.
                source_markdown=content_markdown,
            )
        except HTTPException as exc:
            detail = exc.detail
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
            return {
                "status": "failed",
                "error": f"The document was created but could not be saved: {message}",
                "filename": out_name,
                "templateUsed": template_label,
                "sourceMarkdown": content_markdown,
            }
        except Exception as exc:
            # NEVER-SILENT #8. Only HTTPException was caught here, so anything
            # else — a Firestore rejection, a GCS blip — escaped the tool, blew
            # up the whole ADK run ("Background execution error") and left the
            # user with a dead chat turn and no idea why. A failure to SAVE is
            # still a result: report it, keep the markdown, let the turn finish.
            log.exception("generate_document: ingest failed for %s", out_name)
            return {
                "status": "failed",
                "error": (
                    f"The document was created but could not be saved to your workspace: {type(exc).__name__}: {exc}"
                ),
                "filename": out_name,
                "templateUsed": template_label,
                "sourceMarkdown": content_markdown,
            }

        return {
            "status": "created",
            "docId": saved.doc_id,
            "filename": saved.original_filename,
            "folderId": saved.folder_id,
            "folder": CREATED_FOLDER_NAME,
            "target": target,
            "templateUsed": template_label,
            "referenceDocApplied": outcome.reference_doc_applied,
            "templatePartsCarried": outcome.template_parts_carried,
            "blockCount": saved.blocks_count,
            "parseStatus": saved.status,
            "sizeBytes": len(outcome.content or b""),
        }
    finally:
        if template_path:
            shutil.rmtree(os.path.dirname(template_path), ignore_errors=True)


def _skill_id(tool_context: Any) -> str:
    try:
        return str(tool_context.state.get("skill_id") or "") if tool_context else ""
    except Exception:
        return ""


def _session_id(tool_context: Any) -> str:
    try:
        inv = getattr(tool_context, "_invocation_context", None)
        session = getattr(inv, "session", None) if inv else None
        return str(getattr(session, "id", "") or "")
    except Exception:
        return ""


# --- HTTP surface -----------------------------------------------------------
#
# `POST /api/documents/generate` mirrors `/upload`: same auth, same response
# model, same folder semantics. It exists so the CLI and any non-chat client can
# drive generation without a chat turn — the fast inner loop for template work
# (axiom 7: API first).

from typing import Annotated  # noqa: E402

from fastapi import APIRouter, Depends  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from auth import User, get_current_user  # noqa: E402
from tools.documents.ingest import ParsedDocumentResponse  # noqa: E402

router = APIRouter(prefix="/api/documents", tags=["documents"])

_CurrentUser = Annotated[User, Depends(get_current_user)]


class GenerateDocumentRequest(BaseModel):
    content_markdown: str = Field(alias="contentMarkdown")
    filename: str = "document.docx"
    target: str = "docx"
    template: str = ""
    folder: str = CREATED_FOLDER_NAME
    # Native formatting — deterministic, no model call. Both require a template:
    # they select WITHIN one (which section supplies page setup/headers, and
    # which of its table styles generated tables bind to).
    reference_section: int | None = Field(default=None, alias="referenceSection")
    table_style: str = Field(default="", alias="tableStyle")

    model_config = {"populate_by_name": True}


def _template_provenance(
    template_doc_id: str,
    template_label: str,
    target: str,
    via: str,
    outcome: ConvertOutcome,
) -> dict[str, Any]:
    """What styled this document, recorded so the UI can say so afterwards.

    Stored the moment the bytes are written, because it is unrecoverable later:
    a restyled `.docx` carries the template's theme and header, but nothing in
    the file says WHICH document those came from.

    `templateFilename` is the friendly name and `templateDocId` the address
    (CLAUDE.md #9 — resolve friendly→id, present friendly). Before this, only
    the id was kept, so the Formatting panel had nothing it could show a human
    and reset to "Plain (no template)" on every reopen — the document looked
    un-styled even though it had just been styled.

    `referenceDocApplied` / `templatePartsCarried` are the converter's own
    verdict, kept because "applied, carried 0 parts" is a legitimate outcome for
    a template with nothing to give, and is worth telling the user apart from a
    template that did not apply at all.
    """
    applied = bool(outcome.reference_doc_applied)
    carried = int(outcome.template_parts_carried or 0)
    if applied and carried == 0:
        # "Applied, carried nothing" is how a styling regression looks from the
        # outside: a 200, a document, and no styling. It stayed invisible for a
        # day because nothing said it out loud. It is legitimate for a template
        # with nothing to give, so this is a WARN and not a failure.
        log.warning(
            "template %r applied to %s but carried 0 parts — the document is unstyled (via=%s)",
            template_label or template_doc_id,
            target,
            via,
        )
    return {
        "templateDocId": template_doc_id,
        "templateFilename": template_label,
        "referenceDocApplied": applied,
        "templatePartsCarried": carried,
        "target": target,
        "via": via,
    }


def _with_template_outcome(
    saved: ParsedDocumentResponse,
    template_label: str,
    outcome: ConvertOutcome,
) -> ParsedDocumentResponse:
    """Carry the converter's template verdict out through the HTTP response."""
    return saved.model_copy(
        update={
            "template_used": template_label,
            "reference_doc_applied": bool(outcome.reference_doc_applied),
            "template_parts_carried": int(outcome.template_parts_carried or 0),
        }
    )


def _tenant_not_provisioned_error() -> HTTPException:
    from tools.documents.ingest import _tenant_not_provisioned

    return _tenant_not_provisioned()


async def _generate_into_workspace(
    *,
    user: User,
    content_markdown: str,
    filename: str,
    target: str,
    template: str,
    folder_id: str | None = None,
    folder_name: str = CREATED_FOLDER_NAME,
    reference_section: int | None = None,
    table_style: str = "",
    via: str = "api",
    exclude_doc_id: str = "",
) -> ParsedDocumentResponse:
    """Convert markdown and put the result in the workspace.

    The single implementation behind BOTH `POST /generate` and
    `POST /{doc_id}/revise`. They differ only in where the markdown and the
    destination folder come from — a revision reuses the existing document's
    filename and folder so `ingest_document`'s dedup updates it in place instead
    of creating a second copy.

    Keeping this one function is the same rule the v6.32.0 ingest extraction
    exists to enforce, one level up: two entrances, one implementation, so they
    cannot drift.

    Template resolution goes through `_choose_template`, so a revision applies the
    tenant house style by default exactly as generation does. It did not, and the
    result was that editing one passage of an on-brand document silently returned
    it to Word's defaults with no way back.
    """
    target_fmt = (target or "docx").lower().lstrip(".")
    domain = getattr(user, "domain", "") or ""
    template_path = ""
    template_doc_id = ""
    template_label = ""
    record, reason = await asyncio.to_thread(
        _choose_template,
        uid=user.uid,
        domain=domain,
        wanted=template,
        target=target_fmt,
        exclude_doc_id=exclude_doc_id,
    )
    if reason == "not-found":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TEMPLATE_NOT_FOUND",
                "message": (
                    f"No document called “{template.strip()}” is in your workspace, "
                    "and your organisation has no shared template by that name."
                ),
            },
        )
    if record is not None:
        template_doc_id = str(record.get("__id") or "")
        # The name the user picked, resolved to the name the workspace knows it
        # by. This is what gets stored and shown (CLAUDE.md #9) — the doc-id is
        # stored alongside it for addressing only.
        template_label = str(record.get("originalFilename") or template.strip())
        template_path = await asyncio.to_thread(_download_to_tempfile, str(record.get("sourceUrl") or "")) or ""
        if not template_path and reason == "house-style":
            # Fail soft, as the tool path does: an unreadable house style must
            # never block a revision the user did not ask to have styled.
            log.warning("house style for %s could not be read; revising unstyled", domain)
            template_doc_id = ""
            template_label = ""
        elif not template_path:
            raise HTTPException(
                status_code=502,
                detail={"code": "TEMPLATE_UNREADABLE", "message": "The template could not be read from storage."},
            )

    target = target_fmt
    out_name = _ensure_extension(filename, target)
    try:
        outcome = await convert_markdown(
            content_markdown,
            target=target,
            filename=out_name,
            reference_doc_path=template_path,
            # Only meaningful with a template; the converter refuses them
            # otherwise, with a named error rather than silently ignoring them.
            reference_section=reference_section if template_path else None,
            table_style=table_style if template_path else "",
        )
        if not outcome.ok:
            raise HTTPException(
                status_code=422,
                detail={"code": "CONVERSION_FAILED", "message": outcome.error or "Conversion failed."},
            )

        destination = folder_id or await asyncio.to_thread(
            folders_db.ensure_named_folder, user.uid, folder_name or CREATED_FOLDER_NAME, True
        )
        saved = await ingest_document(
            user=user,
            file_bytes=outcome.content or b"",
            filename=out_name,
            content_type=outcome.content_type,
            folder_id=destination,
            source="generated",
            generated_from=_template_provenance(template_doc_id, template_label, target, via, outcome),
            source_markdown=content_markdown,
        )
        return _with_template_outcome(saved, template_label, outcome)
    finally:
        if template_path:
            shutil.rmtree(os.path.dirname(template_path), ignore_errors=True)


@router.post("/generate")
async def generate_document_route(user: _CurrentUser, body: GenerateDocumentRequest) -> ParsedDocumentResponse:
    """Generate a document from markdown and save it to the caller's workspace."""
    return await _generate_into_workspace(
        user=user,
        content_markdown=body.content_markdown,
        filename=body.filename,
        target=body.target,
        template=body.template,
        folder_name=body.folder,
        reference_section=body.reference_section,
        table_style=body.table_style,
        via="api",
    )


# --- Version history + revision (v6.34.0 DOC-WORKBENCH P3) ------------------


class ReviseDocumentRequest(BaseModel):
    """A revision, expressed as new content rather than a patch.

    There is no write path into a `.docx` — `ailang-parse` exposes parse and
    convert and nothing else — so a revision cannot surgically mutate the binary.
    It regenerates from markdown. `content_markdown` is the whole new body;
    omitting it and passing only `template` restyles the existing content, which
    is the common case and costs no model call at all.
    """

    content_markdown: str = Field(default="", alias="contentMarkdown")
    template: str | None = None
    target: str = ""
    reference_section: int | None = Field(default=None, alias="referenceSection")
    table_style: str = Field(default="", alias="tableStyle")

    model_config = {"populate_by_name": True}


@router.get("/{doc_id}/versions")
async def list_document_versions(doc_id: str, user: _CurrentUser) -> dict[str, Any]:
    """Every stored version of a document, oldest first.

    Versions live in the caller's own per-client bucket (see versions.py), so
    the ownership check here is the same gate as everywhere else.
    """
    from adk.agui import APP_NAME
    from db.clients import resolve_documents_bucket
    from db.firestore import get_document
    from tools.documents.versions import list_versions

    doc = get_document(_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="You don't have access to this document")

    try:
        bucket_name = resolve_documents_bucket(user)
    except Exception as exc:
        raise _tenant_not_provisioned_error() from exc

    versions = await list_versions(
        bucket_name=bucket_name,
        app_name=APP_NAME,
        user_id=user.uid,
        doc_id=doc_id,
        filename=str(doc.get("originalFilename") or ""),
    )
    return {
        "docId": doc_id,
        "filename": doc.get("originalFilename") or "",
        "currentVersion": doc.get("currentVersion"),
        "versions": versions,
    }


async def _restyle_from_original(
    *,
    user: User,
    doc: dict[str, Any],
    doc_id: str,
    template: str,
    target: str,
    reference_section: int | None,
    table_style: str,
) -> ParsedDocumentResponse:
    """Re-render an existing document in a template's styling.

    Used when there is no authored markdown — an uploaded file. The document's
    own bytes are the input, so nothing has to be reconstructed and no model is
    involved: `convert` parses then generates, and `referenceDoc` supplies the
    styles, theme, fonts, headers and page setup.
    """
    source_url = str(doc.get("sourceUrl") or "")
    if not source_url.startswith("gs://"):
        raise HTTPException(status_code=404, detail="This document has no stored file to restyle.")

    filename = str(doc.get("originalFilename") or "document.docx")
    normalised_target = (target or "docx").lower().lstrip(".")
    domain = getattr(user, "domain", "") or ""

    template_path = ""
    template_doc_id = ""
    template_label = ""
    record, reason = await asyncio.to_thread(
        _choose_template,
        uid=user.uid,
        domain=domain,
        wanted=template,
        target=normalised_target,
        # A restyle whose template resolves to the document itself re-reads the
        # bytes it is about to overwrite: applied, nothing carried, styling gone.
        exclude_doc_id=doc_id,
    )
    if reason == "not-found":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TEMPLATE_NOT_FOUND",
                "message": (
                    f"No document called “{template.strip()}” is in your workspace, "
                    "and your organisation has no shared template by that name."
                ),
            },
        )
    if record is not None:
        template_doc_id = str(record.get("__id") or "")
        template_label = str(record.get("originalFilename") or template.strip())
        template_path = await asyncio.to_thread(_download_to_tempfile, str(record.get("sourceUrl") or "")) or ""
        if not template_path and reason == "house-style":
            log.warning("house style for %s could not be read; restyling unstyled", domain)
            template_doc_id = ""
            template_label = ""
        elif not template_path:
            raise HTTPException(
                status_code=502,
                detail={"code": "TEMPLATE_UNREADABLE", "message": "The template could not be read from storage."},
            )

    original_path = await asyncio.to_thread(
        _download_to_tempfile, source_url, PurePosixPath(filename).suffix or ".docx"
    )
    if not original_path:
        raise HTTPException(status_code=502, detail="The document could not be read from storage.")

    try:
        outcome = await convert_document_file(
            original_path,
            target=normalised_target,
            reference_doc_path=template_path,
            reference_section=reference_section if template_path else None,
            table_style=table_style if template_path else "",
        )
        if not outcome.ok:
            raise HTTPException(
                status_code=422,
                detail={"code": "CONVERSION_FAILED", "message": outcome.error or "Restyle failed."},
            )
        saved = await ingest_document(
            user=user,
            file_bytes=outcome.content or b"",
            filename=_ensure_extension(filename, normalised_target),
            content_type=outcome.content_type,
            folder_id=str(doc.get("folderId") or "") or None,
            source=str(doc.get("source") or "uploaded"),
            generated_from=_template_provenance(template_doc_id, template_label, normalised_target, "restyle", outcome),
            # No authored markdown to carry: this document still has none.
            source_markdown=str(doc.get("sourceMarkdown") or ""),
        )
        return _with_template_outcome(saved, template_label, outcome)
    finally:
        for path in (original_path, template_path):
            if path:
                shutil.rmtree(os.path.dirname(path), ignore_errors=True)


@router.post("/{doc_id}/revise")
async def revise_document(doc_id: str, user: _CurrentUser, body: ReviseDocumentRequest) -> ParsedDocumentResponse:
    """Regenerate a document in place, keeping its history.

    Reuses the SAME filename, so `ingest_document`'s dedup path updates the
    existing record rather than creating a second one — that in-place update is
    what makes an iterate loop feel like editing. The previous rendering stays
    retrievable as an earlier version.
    """
    from db.firestore import get_document

    doc = get_document(_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="You don't have access to this document")

    markdown = body.content_markdown or str(doc.get("sourceMarkdown") or "")
    filename = str(doc.get("originalFilename") or "document.docx")

    if not markdown.strip():
        # No authored source. That is fine for a RESTYLE — applying a template is
        # a pure conversion of the document that already exists, and `convert`
        # parses-then-generates, so docx → docx with a referenceDoc re-renders the
        # same content in the template's styling. This used to refuse outright,
        # which made "change the formatting" impossible on an uploaded document —
        # the common case, since a letterhead is usually applied to something
        # somebody sent you.
        #
        # (A caller that SUPPLIES content is handled above — `markdown` is then
        # non-empty and the ordinary generate path applies it. The only case
        # reaching here is "no new content and nothing stored", i.e. a restyle.
        # `/revise-passage` keeps its own refusal: a splice genuinely needs a
        # source to splice into.)
        return await _restyle_from_original(
            user=user,
            doc=doc,
            doc_id=doc_id,
            template=body.template if body.template is not None else "",
            target=(body.target or str(doc.get("sourceFormat") or "docx")),
            reference_section=body.reference_section,
            table_style=body.table_style,
        )

    target = (body.target or str(doc.get("sourceFormat") or "docx")).lower().lstrip(".")
    template = body.template if body.template is not None else ""

    return await _generate_into_workspace(
        user=user,
        content_markdown=markdown,
        filename=filename,
        target=target,
        template=template,
        folder_id=str(doc.get("folderId") or "") or None,
        reference_section=body.reference_section,
        table_style=body.table_style,
        via="revise",
        exclude_doc_id=doc_id,
    )


@router.get("/templates")
def list_document_templates(user: _CurrentUser, exclude: str = "") -> dict[str, Any]:
    """The documents that can be used as a template: the caller's, and the tenant's.

    Only `.docx` — no other format has a styling container, so offering a `.pdf`
    as a template would be a control that cannot work.

    The tenant's shared brand templates are listed alongside the caller's own,
    marked `source: "organisation"`. They were missing, and their absence is what
    made a lost house style unrecoverable: the picker could only offer documents
    from the workspace, and the house style is not one of them, so there was
    nothing a user could choose to get their branding back.

    `exclude` is the document being edited. A document offered as its own
    template is a trap — the converter accepts it and carries nothing — so the
    picker does not list it. `_choose_template` refuses it too; this only keeps
    the UI from proposing it.

    Returns FRIENDLY NAMES (CLAUDE.md #9). The picker sends a name back and the
    backend resolves it, so neither the user nor the model ever handles a
    doc-id.
    """
    from db.firestore import query_documents
    from tools.documents.brand_assets import list_brand_assets

    docs = query_documents(_COLLECTION, filters=[("userId", "==", user.uid)], limit=500)
    templates = [
        {
            "filename": str(d.get("originalFilename") or ""),
            "folderId": d.get("folderId"),
            "source": d.get("source") or "uploaded",
        }
        for d in docs
        if str(d.get("originalFilename") or "").lower().endswith(".docx")
        and not (exclude and str(d.get("__id") or "") == exclude)
    ]

    domain = getattr(user, "domain", "") or ""
    if domain:
        assets = list_brand_assets(domain, "templates")
        if assets.ok:
            known = {t["filename"].casefold() for t in templates}
            for name in assets.names:
                if name.lower().endswith(".docx") and name.casefold() not in known:
                    templates.append({"filename": name, "folderId": None, "source": "organisation"})
        else:
            # Never fatal: a tenant may simply have no brand templates. Logged so
            # a misconfigured environment is findable rather than silently empty.
            log.info("brand templates unavailable for %s: %s", domain, assets.error)

    templates.sort(key=lambda t: t["filename"].casefold())
    return {"templates": templates}


# --- Targeted passage revision (v6.34.0 P4b) --------------------------------
#
# Regenerating a whole document to change one sentence is slow AND unreliable:
# the model has to reproduce every other paragraph verbatim, which costs a full
# pass over the document and silently risks changes nobody asked for. Observed
# on test 2026-09-01 — a passage edit on a 91-block document took ~2 minutes.
#
# Splicing instead: the model sees ONLY the selected passage, rewrites ONLY that,
# and the result is substituted into the stored markdown by exact string match.
# Fast (a few hundred tokens instead of the document), and the rest of the
# document is untouched BY CONSTRUCTION rather than by the model's good behaviour.


class RevisePassageRequest(BaseModel):
    anchor_text: str = Field(alias="anchorText")
    instruction: str

    model_config = {"populate_by_name": True}


def _normalise(text: str) -> str:
    return " ".join(text.split())


# Markdown syntax that carries no visible text. A user selects the RENDERED
# document — "Summary", not "## Summary"; "the term" not "**the term**" — so a
# literal search of the source finds nothing for most real selections. That was
# the PASSAGE_NOT_FOUND reported on test 2026-09-01.
_SYNTAX_AT_LINE_START = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+\.[ \t]+|>[ \t]?)")
_INLINE_SYNTAX = re.compile(r"\*\*|__|\*|_|`|~~")


def _plain_projection(markdown: str) -> tuple[str, list[int]]:
    """Project markdown to its visible text, keeping a map back to the source.

    Returns ``(plain, index_map)`` where ``plain`` is whitespace-collapsed
    visible text and ``index_map[i]`` is the offset in ``markdown`` of
    ``plain[i]``. That map is what lets a match on rendered text be turned back
    into an exact source span, so the splice replaces precisely the original
    characters — syntax included.
    """
    plain: list[str] = []
    index_map: list[int] = []
    pos = 0
    for line in markdown.splitlines(keepends=True):
        body_start = pos
        stripped = line.rstrip("\n")

        # Drop leading block syntax (heading hashes, bullet, quote marker).
        lead = _SYNTAX_AT_LINE_START.match(stripped)
        i = lead.end() if lead else 0

        while i < len(stripped):
            ch = stripped[i]
            # Inline emphasis / code markers carry no text.
            m = _INLINE_SYNTAX.match(stripped, i)
            if m:
                i = m.end()
                continue
            # Table pipes read as cell separators, not content.
            if ch == "|":
                if plain and plain[-1] != " ":
                    plain.append(" ")
                    index_map.append(body_start + i)
                i += 1
                continue
            if ch.isspace():
                if plain and plain[-1] != " ":
                    plain.append(" ")
                    index_map.append(body_start + i)
                i += 1
                continue
            plain.append(ch)
            index_map.append(body_start + i)
            i += 1

        # A line break is whitespace in the rendered document.
        if plain and plain[-1] != " ":
            plain.append(" ")
            index_map.append(body_start + len(stripped))
        pos += len(line)

    return "".join(plain), index_map


def locate_passage(source_markdown: str, anchor: str) -> str | None:
    """The exact substring of `source_markdown` the user selected, or None.

    Three attempts, cheapest first:
      1. the selection appears verbatim (it was plain prose);
      2. it matches once whitespace is collapsed (line wrapping);
      3. it matches the document's VISIBLE text — headings without their hashes,
         bullets without their markers, bold without its asterisks — which is
         what a person actually selects.

    Always returns the ORIGINAL slice, so the splice puts back exactly what was
    there rather than a normalised approximation.
    """
    anchor = (anchor or "").strip()
    if not anchor or not source_markdown:
        return None
    if anchor in source_markdown:
        return anchor

    needle = _normalise(anchor)
    if not needle:
        return None

    # (2) whitespace-only differences, e.g. a wrapped paragraph.
    lines = source_markdown.split("\n")
    for start in range(len(lines)):
        acc = ""
        for end in range(start, min(start + 60, len(lines))):
            acc = lines[start] if end == start else f"{acc}\n{lines[end]}"
            if _normalise(acc) == needle:
                return acc
            if len(_normalise(acc)) > len(needle) + 120:
                break

    # (3) the selection is rendered text; the source carries markdown syntax.
    plain, index_map = _plain_projection(source_markdown)
    hit = plain.find(needle)
    if hit == -1:
        # Case-insensitive last resort — a selection can pick up a capitalised
        # first letter from a styled run.
        hit = plain.casefold().find(needle.casefold())
    if hit == -1 or not index_map:
        return None

    end_index = min(hit + len(needle), len(index_map)) - 1
    if end_index < hit:
        return None
    return source_markdown[index_map[hit] : index_map[end_index] + 1]


async def rewrite_passage(passage: str, instruction: str) -> str:
    """Ask the model to rewrite ONE passage. Returns the new text.

    Goes through the resilient seam (backend/CLAUDE.md: every model call needs
    retry + fallback). The prompt is deliberately narrow — the model never sees
    the rest of the document, so it cannot change it.
    """
    from tools.resilient_genai import generate_content_resilient

    prompt = (
        "Rewrite the passage below according to the instruction.\n\n"
        "Rules:\n"
        "- Return ONLY the rewritten passage. No preamble, no explanation, no code fence.\n"
        "- Keep the same Markdown structure (heading levels, list markers, table pipes).\n"
        "- Do not add or remove sections.\n\n"
        f"INSTRUCTION: {instruction}\n\n"
        "PASSAGE:\n"
        f"{passage}"
    )
    # `lite`, not `pro` (axiom 4). Rewording one passage to an instruction is a
    # writing tweak, not reasoning: the model never sees the rest of the
    # document and cannot restructure it. Measured on test, a `pro` rewrite of a
    # single paragraph cost ~14s of the ~16s the user spent waiting, which is
    # most of a silence that no amount of UI can make pleasant.
    result = await generate_content_resilient(
        prompt=prompt,
        model_ref="lite",
        config={"max_output_tokens": 4096},
        progress_label="Rewriting the passage…",
        label="passage-revision",
    )
    text = (getattr(result, "text", None) or "").strip()
    # A fenced reply is common despite the instruction; unwrap rather than
    # splicing a code fence into the document.
    if text.startswith("```"):
        body = text.split("\n", 1)[1] if "\n" in text else ""
        text = body.rsplit("```", 1)[0].strip()
    return text


async def ensure_source_markdown(doc_id: str, doc: dict[str, Any]) -> str:
    """The document's editable Markdown source, deriving it if there is none.

    A document the assistant wrote stores the Markdown it was rendered from. An
    UPLOADED document has no authored source — which used to mean passages in it
    could not be edited at all ("This document has no editable source"), even
    though editing someone else's document is the common case.

    There is still no write path into a .docx, but `convert` goes both ways: the
    file can be rendered back to Markdown, which then becomes the editable source
    for splicing. Derived once and persisted, so the next edit is immediate and —
    more importantly — every later edit splices into the SAME text the previous
    one produced, rather than re-deriving a slightly different document each time.
    """
    existing = str(doc.get("sourceMarkdown") or "")
    if existing.strip():
        return existing

    source_url = str(doc.get("sourceUrl") or "")
    if not source_url.startswith("gs://"):
        return ""

    filename = str(doc.get("originalFilename") or "document.docx")
    local = await asyncio.to_thread(_download_to_tempfile, source_url, PurePosixPath(filename).suffix or ".docx")
    if not local:
        return ""
    try:
        outcome = await convert_document_file(local, target="md")
        if not outcome.ok or not outcome.content:
            return ""
        markdown = outcome.content.decode("utf-8", errors="replace")
        if not markdown.strip():
            return ""
        # Persist so this is a one-off cost and later edits build on each other.
        try:
            from db.firestore import update_document

            await asyncio.to_thread(update_document, _COLLECTION, doc_id, {"sourceMarkdown": markdown})
        except Exception as exc:  # pragma: no cover — caching is best-effort
            log.warning("could not persist derived source for %s: %s", doc_id, exc)
        return markdown
    finally:
        shutil.rmtree(os.path.dirname(local), ignore_errors=True)


@router.post("/{doc_id}/revise-passage")
async def revise_passage(doc_id: str, user: _CurrentUser, body: RevisePassageRequest) -> ParsedDocumentResponse:
    """Rewrite one passage and splice it back, leaving the rest untouched."""
    return await _revise_passage_impl(doc_id, user, body)


@router.post("/{doc_id}/revise-passage/stream")
async def revise_passage_stream(doc_id: str, user: _CurrentUser, body: RevisePassageRequest) -> Any:
    """The same rewrite, as SSE, so the wait is not a silence (#8).

    `_revise_passage_impl` already emits three progress labels through
    `_progress` / `progress_label`. On the plain JSON route they went NOWHERE:
    `emit_stage_progress` enqueues onto the LatencyTracker bound to the async
    context, nothing binds one on an ordinary POST, so `get_current_tracker()`
    returned the module NULL tracker and every label was written and dropped.
    That is the same trap CLAUDE.md documents for `emit_a2ui_surface` — the work
    was done, the transport was missing.

    So this binds a tracker exactly as `stream_skill` does, runs the impl as a
    task, and drains the queue while it works. The labels the backend already
    produces become the UI's progress, rather than the UI inventing its own.

    Frames: `{"type":"progress","label":…}` … then exactly one terminal frame,
    `{"type":"done","document":…}` or `{"type":"error","code":…,"message":…}`.
    A terminal frame is guaranteed even on an unexpected failure, because a
    stream that just stops is the silence this endpoint exists to remove.
    """
    import asyncio as _asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    from observability.timing import LatencyTracker, reset_current_tracker, set_current_tracker

    async def frames():
        tracker = LatencyTracker(skill_id="document-writer", session_id="", user_id=user.uid)
        token = set_current_tracker(tracker)
        task = _asyncio.create_task(_revise_passage_impl(doc_id, user, body))
        try:
            while True:
                done, _ = await _asyncio.wait({task}, timeout=0.25)
                for event in tracker.drain_stage_events():
                    label = (getattr(event, "value", None) or {}).get("label")
                    if label:
                        yield f"data: {_json.dumps({'type': 'progress', 'label': label})}\n\n"
                if done:
                    break

            saved = await task
            payload = saved.model_dump(by_alias=True) if hasattr(saved, "model_dump") else {}
            yield f"data: {_json.dumps({'type': 'done', 'document': payload}, default=str)}\n\n"
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            body_ = {"type": "error", "status": exc.status_code, **detail}
            yield f"data: {_json.dumps(body_, default=str)}\n\n"
        except Exception as exc:
            log.exception("revise-passage stream failed")
            yield (
                "data: "
                + _json.dumps({"type": "error", "status": 500, "message": f"The rewrite failed: {exc}"})
                + "\n\n"
            )
        finally:
            reset_current_tracker(token)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _revise_passage_impl(doc_id: str, user: User, body: RevisePassageRequest) -> ParsedDocumentResponse:
    """One implementation behind both revise-passage entrances."""
    from db.firestore import get_document

    doc = get_document(_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="You don't have access to this document")

    _progress("Preparing the document…")
    source_markdown = await ensure_source_markdown(doc_id, doc)
    if not source_markdown.strip():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_EDITABLE_SOURCE",
                "message": (
                    "This document's text could not be read back for editing. It may be a scan or an image-only PDF."
                ),
            },
        )

    passage = locate_passage(source_markdown, body.anchor_text)
    if passage is None:
        # Say so rather than guessing at a location — a wrong splice edits the
        # wrong paragraph, which is worse than refusing (NEVER-SILENT #8).
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PASSAGE_NOT_FOUND",
                "message": (
                    "That passage could not be located in the document's source. It may have "
                    "changed since the document was generated — try selecting it again."
                ),
            },
        )

    # Strip here rather than trusting the helper: a whitespace-only reply is
    # "empty" for splicing purposes, and splicing blank text would silently
    # delete the passage the user asked to improve.
    rewritten = ((await rewrite_passage(passage, body.instruction)) or "").strip()
    if not rewritten:
        raise HTTPException(
            status_code=502,
            detail={"code": "REWRITE_EMPTY", "message": "The rewrite came back empty; the document is unchanged."},
        )

    spliced = source_markdown.replace(passage, rewritten, 1)

    return await _generate_into_workspace(
        user=user,
        content_markdown=spliced,
        filename=str(doc.get("originalFilename") or "document.docx"),
        target=str(doc.get("sourceFormat") or "docx"),
        template="",
        folder_id=str(doc.get("folderId") or "") or None,
        via="revise-passage",
    )


@router.post("/{doc_id}/restore/{version}")
async def restore_document_version(doc_id: str, version: int, user: _CurrentUser) -> ParsedDocumentResponse:
    """Make an earlier version the current one.

    Non-destructive: restoring re-ingests the old bytes under the same filename,
    so the dedup path updates the document in place AND the restore itself
    becomes a new version. Nothing is lost, and the history is a record of what
    happened rather than a stack that can be popped.

    This is what makes the history clickable — the viewer renders the CURRENT
    document from Firestore, so restoring is how an older version gets back on
    screen. Downloading a version shows it; restoring it puts it back.
    """
    from adk.agui import APP_NAME
    from db.clients import resolve_documents_bucket
    from db.firestore import get_document
    from tools.documents.versions import load_version

    doc = get_document(_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="You don't have access to this document")

    filename = str(doc.get("originalFilename") or "document.docx")
    try:
        bucket_name = resolve_documents_bucket(user)
    except Exception as exc:
        raise _tenant_not_provisioned_error() from exc

    data = await load_version(
        bucket_name=bucket_name,
        app_name=APP_NAME,
        user_id=user.uid,
        doc_id=doc_id,
        filename=filename,
        version=version,
    )
    if not data:
        raise HTTPException(
            status_code=404,
            detail={"code": "VERSION_NOT_FOUND", "message": f"Version {version} is no longer available."},
        )

    return await ingest_document(
        user=user,
        file_bytes=data,
        filename=filename,
        content_type=str(doc.get("contentType") or "") or None,
        folder_id=str(doc.get("folderId") or "") or None,
        source=str(doc.get("source") or "generated"),
        generated_from={"restoredFromVersion": version, "via": "restore"},
        # The stored source belongs to the CURRENT text, which we are replacing;
        # carrying it forward would let a later passage-splice edit markdown that
        # no longer matches the document on screen.
        source_markdown="",
    )
