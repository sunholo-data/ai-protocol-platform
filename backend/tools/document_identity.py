"""Confirm a resolved document is the one the user actually named.

PRECUT-HARDENING M1b. The failure this exists to stop, observed live on
2026-08-09: a user asked to compare two contracts by name, one of them had never
been parsed into the workspace, the semantic search returned the nearest chunk
from a *different* contract, and the tools happily extracted and compared it. The
answer was published under the name the user had said. Nothing anywhere checked
that the document opened was the document requested.

Why the tool layer and not the prompt: the tools validate only that a doc_id
*exists* (a `KeyError` on a missing record), and the result payloads carry an
opaque UUID and no filename — so the model has no in-band way to notice it opened
the wrong file, while the human sees the correct filename on the workbench tab.
That asymmetry is exactly why the wrong-document answer reads as confident.

Why not a similarity threshold instead: the corpus is nearest-K over COSINE
DISTANCE, and different PPAs share near-identical legal boilerplate, so "a force
majeure clause from another contract" sits genuinely close to the one asked for.
No distance cutoff cleanly separates *this contract* from *one that reads like
it*. Identity is a name question, so it is answered on names.

**The asymmetry is deliberate.** A false refusal costs a round trip and names the
actual file, so the model can retry or ask. A false accept is an authoritative
analysis of the wrong contract — the trust-ending outcome. When those two trade
off, this module refuses.
"""

from __future__ import annotations

import re

# Words that carry no identifying power for a contract: every PPA is a "PPA", an
# "agreement" or a "contract", and the extension is not part of the name. Matching
# on these would let any contract satisfy any other contract's name.
_NOISE = {
    "ppa",
    "ppas",
    "agreement",
    "agreements",
    "contract",
    "contracts",
    "document",
    "doc",
    "the",
    "a",
    "an",
    "of",
    "for",
    "and",
    "with",
    "pdf",
    "docx",
    "txt",
    "md",
    "final",
    "draft",
    "copy",
    "v1",
    "v2",
}

_WORD = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    """Lowercase and collapse punctuation to spaces, so separators stop mattering.

    ``2024_Northwind_ACME_Iberia.pdf`` and ``2024 northwind acme iberia pdf``
    must compare identically — real filenames use underscores, hyphens, and
    inconsistent spacing.
    """
    return " ".join(_WORD.findall(text.lower()))


def _distinctive_tokens(name: str) -> list[str]:
    return [t for t in _normalise(name).split() if t not in _NOISE]


def resolve_filename(doc_id: str | None) -> str:
    """Best-effort ``doc_id`` -> ``originalFilename``, for checking identity cheaply.

    One small Firestore read, and only on the path where a name was actually
    supplied. Exists so the identity check can run BEFORE an extraction cache
    lookup: the cache is keyed on the document id alone, so checking after it
    would let a warm cache serve a wrong-document result with the guard skipped
    entirely — the guard would then only work on the first call of a session.

    Fail-soft: any lookup problem returns "" so ``identity_mismatch`` declines to
    judge, rather than inventing a mismatch and blocking a legitimate document.
    """
    if not doc_id:
        return ""
    try:
        from db.firestore import get_document

        raw = get_document("parsed_documents", doc_id) or {}
        return raw.get("originalFilename") or ""
    except Exception:
        return ""


def identity_mismatch(expected_name: str | None, actual_filename: str | None) -> str | None:
    """Return a refusal message when the document is not the one named, else None.

    ``expected_name`` is the name the USER used, in their own words ("the
    Brightfield — Iberia PPA"). ``actual_filename`` is what the id resolved to.

    NOTE the examples below are FICTIONAL stand-ins. The real pair that produced
    this bug is customer contract data and must not ship in the public template —
    naming it here is what tripped the publish gate the first time this landed.

    Matching is substring-per-token rather than set equality, so a user's partial
    or reworded name still matches a real filename:

        "Brightfield PPA"      vs "Brightfield PPA model Iberia.pdf"  -> match
        "Vantage Grid PPA"        vs "Vantage Grid Power Purchase ….pdf"   -> match
        "Brightfield — Iberia"  vs "2024_Northwind_ACME_Iberia.pdf"     -> MISMATCH

    The last one is the live bug: the geography word is shared, but the
    distinctive part of the name is nowhere in the resolved file — and one shared
    word is not identity.

    Returns None when ``expected_name`` is blank — the guard is opt-in, because a
    required parameter would force the model to invent a value (see the
    REQUIRED-param trap in ``backend/adk/CLAUDE.md``).
    """
    if not expected_name or not expected_name.strip():
        return None
    if not actual_filename or not actual_filename.strip():
        return None

    wanted = _distinctive_tokens(expected_name)
    if not wanted:
        # The user's name was ALL noise ("the contract"). It cannot confirm or deny
        # identity, so it must not be treated as confirmation.
        return None

    haystack = _normalise(actual_filename)
    missing = [t for t in wanted if t not in haystack]
    if not missing:
        return None

    return (
        f"Refusing to analyse this document: it does not appear to be the one you named. "
        f"You asked for {expected_name!r}, but this id resolves to {actual_filename!r}, "
        f"which is missing: {', '.join(missing)}. "
        f"Do NOT present this document's contents under the name {expected_name!r}. "
        f"Use list_documents to find the right document. If it is NOT listed there, "
        f"it is probably still in the tenant bucket and simply unparsed — find it with "
        f"list_bucket_documents and pass gs_url= instead of doc_id=, which parses it "
        f"on the fly. Only say you cannot find it when it is in NEITHER place. Never "
        f"answer from a different contract."
    )
