"""PPA result → A2UI transforms (tool-results-as-a2ui / 7.3, Model B — M2).

Registers the first two result→A2UI mappings against the shared registry in
``adk.a2ui_result_render``:

  * ``compare_ppa_contracts`` (typed ``PpaComparison``) → a tabbed compare
    surface: top-level ``Tabs`` [Key Differences · Contract A · Contract B].
    The Key Differences tab carries a nested ``Tabs`` severity filter
    (All / Material / Moderate / Cosmetic) — pure-client tab switching, no
    round trip. Each diff row is a ``Card`` built once and referenced from the
    "All" tab and its severity tab, with ``block_id`` citations.
  * ``extract_ppa_clauses`` (typed ``PpaClauses``) → a clause-card surface so
    extract-only skills (e.g. one-ppa-expert) get a workbench view too.

Importing this module registers the mappings as a side effect (the
composition root — ``adk.agent`` — imports it once at startup). Both tools keep
returning their typed JSON unchanged; these transforms run server-side, out of
the model's context, and are unit-tested for A2UI v0.9 schema validity.

Why nested Tabs for the severity filter (not a reactive ChoicePicker): the
A2UI v0.9 Basic catalog has no per-component visibility binding and no
``filter``/``map`` expression function, and a List's dynamic-child template
binds a *static* data path — so reactive client-side row filtering is not
expressible. Native ``Tabs`` self-manage selection, so partitioning rows by
severity into tabs gives a zero-latency, protocol-native filter with no bespoke
React. The multi-select *clause* filter (dynamic clause set) rides the M3
surface-action loop.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from adk.a2ui_result_render import (
    BASIC_CATALOG_ID,
    WORKSPACE_SURFACE_ID,
    register,
)

logger = logging.getLogger(__name__)

COMPARE_TOOL = "compare_ppa_contracts"
EXTRACT_TOOL = "extract_ppa_clauses"

# Standard 12-field PpaClauses order (mirrors tools/schemas/ppa_clauses.py) —
# the clause table reads like a PPA negotiation checklist.
_STANDARD_CLAUSE_FIELDS = (
    "counterparty_buyer",
    "counterparty_seller",
    "volume_mwh",
    "term_years",
    "settlement_type",
    "contract_form",
    "price_formula",
    "rtm_provider",
    "force_majeure",
    "change_of_law",
    "termination",
    "governing_law",
)

# Severity partition order for the Key-Differences nested filter tabs.
_SEVERITY_ORDER = ("material", "moderate", "cosmetic")


# --- A2UI v0.9 component-tree builder ---------------------------------------


class _Tree:
    """Accumulates flattened A2UI v0.9 components with unique ids.

    Every ``add_*`` helper appends a ``{id, component, ...}`` dict and returns
    the id, so callers compose by reference (v0.9 children/child/tabs are id
    strings, never inline objects). ``root(children)`` appends the mandatory
    ``id: "root"`` node last.
    """

    def __init__(self) -> None:
        self.components: list[dict[str, Any]] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def text(self, text: str, *, variant: str | None = None, prefix: str = "t") -> str:
        # Avoid variant="caption": the v0.9 React SDK renders it as <caption>,
        # which is only valid inside <table> and triggers a hydration warning
        # (see skills/templates/workspace-demo/SKILL.md). Use "body" instead.
        comp: dict[str, Any] = {"id": self._next_id(prefix), "component": "Text", "text": text}
        if variant:
            comp["variant"] = variant
        self.components.append(comp)
        return comp["id"]

    def column(self, children: list[str], *, prefix: str = "col") -> str:
        cid = self._next_id(prefix)
        self.components.append({"id": cid, "component": "Column", "children": children})
        return cid

    def row(self, children: list[str], *, justify: str | None = None, prefix: str = "row") -> str:
        cid = self._next_id(prefix)
        comp: dict[str, Any] = {"id": cid, "component": "Row", "children": children}
        if justify:
            comp["justify"] = justify
        self.components.append(comp)
        return cid

    def list_(self, children: list[str], *, prefix: str = "list") -> str:
        cid = self._next_id(prefix)
        self.components.append({"id": cid, "component": "List", "children": children})
        return cid

    def card(self, child: str, *, prefix: str = "card") -> str:
        cid = self._next_id(prefix)
        self.components.append({"id": cid, "component": "Card", "child": child})
        return cid

    def tabs(self, tabs: list[tuple[str, str]], *, prefix: str = "tabs") -> str:
        cid = self._next_id(prefix)
        self.components.append(
            {"id": cid, "component": "Tabs", "tabs": [{"title": title, "child": child} for title, child in tabs]}
        )
        return cid

    def datetime_input(
        self,
        *,
        path: str,
        label: str | None = None,
        enable_date: bool = True,
        enable_time: bool = False,
        min_value: str | None = None,
        max_value: str | None = None,
        prefix: str = "dti",
    ) -> str:
        # A2UI v0.9 Basic ``DateTimeInput``. ``value`` binds to the surface data
        # model at ``path`` (``{"path": "/effective_date"}``): when the user
        # picks a date the SDK writes it there, and ``readA2uiSurfaceState``
        # snapshots it into ``forwardedProps.a2ui_surface_state`` on the next
        # surface-action-run — that is how the value reaches the tool re-run.
        cid = self._next_id(prefix)
        comp: dict[str, Any] = {
            "id": cid,
            "component": "DateTimeInput",
            "value": {"path": path},
            "enableDate": enable_date,
            "enableTime": enable_time,
        }
        if label:
            comp["label"] = label
        if min_value:
            comp["min"] = min_value
        if max_value:
            comp["max"] = max_value
        self.components.append(comp)
        return cid

    def text_field(
        self,
        *,
        path: str,
        label: str | None = None,
        validation_regexp: str | None = None,
        prefix: str = "tf",
    ) -> str:
        # A2UI v0.9 Basic ``TextField``. ``value`` binds to the surface data
        # model at ``path``. ``validationRegexp`` constrains input client-side
        # (e.g. digits-only for an amount); the backend re-validates LOUDLY on
        # build (never trust the client for a trust-critical number).
        cid = self._next_id(prefix)
        comp: dict[str, Any] = {"id": cid, "component": "TextField", "value": {"path": path}}
        if label:
            comp["label"] = label
        if validation_regexp:
            comp["validationRegexp"] = validation_regexp
        self.components.append(comp)
        return cid

    def choice_picker(
        self,
        *,
        path: str,
        options: list[Any],
        label: str | None = None,
        multi: bool = False,
        prefix: str = "cp",
    ) -> str:
        # A2UI v0.9 Basic ``ChoicePicker`` (select / multi-select). ``value`` binds
        # to the surface data model at ``path`` (the selected option ``value`` for
        # ``mutuallyExclusive``; a list for ``multipleSelection``). ``options`` is a
        # list of ``(value, label)`` pairs or bare scalars (value == label). Schema
        # verified against a2ui==0.9.x basic_catalog.json (2026-07-14).
        cid = self._next_id(prefix)
        opts: list[dict[str, str]] = []
        for opt in options:
            if isinstance(opt, (tuple, list)) and len(opt) == 2:
                value, lab = opt
            else:
                value = lab = opt
            opts.append({"label": str(lab), "value": str(value)})
        comp: dict[str, Any] = {
            "id": cid,
            "component": "ChoicePicker",
            "value": {"path": path},
            "options": opts,
            "variant": "multipleSelection" if multi else "mutuallyExclusive",
        }
        if label:
            comp["label"] = label
        self.components.append(comp)
        return cid

    def checkbox(self, *, path: str, label: str, prefix: str = "cb") -> str:
        # A2UI v0.9 Basic ``CheckBox`` (boolean toggle). The catalog REQUIRES both
        # ``label`` and ``value``; ``value`` binds a boolean at ``path``. Verified
        # against a2ui==0.9.x basic_catalog.json (2026-07-14).
        cid = self._next_id(prefix)
        self.components.append({"id": cid, "component": "CheckBox", "label": label, "value": {"path": path}})
        return cid

    def button(self, label: str, action: dict[str, Any], *, variant: str | None = None, prefix: str = "btn") -> str:
        # A2UI Button.action fires a surface action. We use the `run:` name
        # convention so A2UISurfaceMount routes it to surface-action-RUN (a full
        # agent turn) rather than the fire-and-forget surface-action — see the
        # per-action routing in A2UISurfaceMount.tsx. variant="primary" picks up
        # --a2ui-primary-color (the app accent).
        label_id = self.text(label, prefix=f"{prefix}-label")
        cid = self._next_id(prefix)
        comp: dict[str, Any] = {"id": cid, "component": "Button", "child": label_id, "action": action}
        if variant:
            comp["variant"] = variant
        self.components.append(comp)
        return cid

    def root(self, children: list[str]) -> None:
        self.components.append({"id": "root", "component": "Column", "children": children})

    def messages(self) -> list[dict[str, Any]]:
        return [
            {"version": "v0.9", "createSurface": {"surfaceId": WORKSPACE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
            {"version": "v0.9", "updateComponents": {"surfaceId": WORKSPACE_SURFACE_ID, "components": self.components}},
        ]


def _is_error(result: Any) -> bool:
    """A tool error result is ``{"error": "..."}`` — never render a surface for it
    (the model narrates the error in chat instead)."""
    return not isinstance(result, dict) or "error" in result


# NOTE on citations: clause/diff block_ids are raw UUIDs — not human-navigable,
# so we do NOT render them as visible text (they were pure noise). They stay in
# the underlying typed result for a future click-to-source affordance.


# --- clause rendering (shared: compare tabs + extract surface) ----------------


def _clause_row(tree: _Tree, clause: dict[str, Any]) -> str:
    """One clause → a compact Row [name | value · confidence].

    Compact by design (fixes the "long unnavigable list"): a single Row per
    clause instead of a padded Card+Column stack, so a 12-clause contract reads
    like a scannable table rather than a wall of cards. The raw block_id is NOT
    shown (a UUID isn't human-navigable — kept in the data for future
    click-to-source).
    """
    name = str(clause.get("display_name") or clause.get("clause_name") or "Clause")
    value = clause.get("value")
    value_text = str(value) if value not in (None, "") else "—"
    confidence = str(clause.get("confidence") or "").strip()
    if confidence:
        value_text = f"{value_text}   ·   {confidence}"
    return tree.row(
        [
            tree.text(name, variant="h5", prefix="cl-name"),
            tree.text(value_text, prefix="cl-val"),
        ],
        justify="spaceBetween",
        prefix="cl-row",
    )


def _clause_rows_for(tree: _Tree, clauses: dict[str, Any]) -> list[str]:
    """All populated clause rows for a PpaClauses dict, in checklist order."""
    row_ids: list[str] = []
    for field in _STANDARD_CLAUSE_FIELDS:
        clause = clauses.get(field)
        if isinstance(clause, dict):
            row_ids.append(_clause_row(tree, clause))
    for clause in clauses.get("other_clauses") or []:
        if isinstance(clause, dict):
            row_ids.append(_clause_row(tree, clause))
    return row_ids


def _clause_section(tree: _Tree, clauses: dict[str, Any], *, heading: str, subtitle: str | None = None) -> str:
    """A titled clause section (used as a compare / extract tab body). Returns a
    Column id: heading (+ optional subtitle) + one Card wrapping a scrollable
    List of compact rows. The raw doc_id is never shown — callers pass a
    resolved filename as ``subtitle`` when the heading isn't already the file."""
    children = [tree.text(heading, variant="h3", prefix="sec-h")]
    if subtitle:
        children.append(tree.text(subtitle, prefix="sec-doc"))
    if clauses.get("other_clauses_truncated"):
        total = clauses.get("other_clauses_total") or 0
        shown = len(clauses.get("other_clauses") or [])
        children.append(tree.text(f"Showing {shown} of {total} non-standard clauses.", prefix="sec-trunc"))

    rows = _clause_rows_for(tree, clauses)
    if rows:
        children.append(tree.card(tree.list_(rows, prefix="sec-list"), prefix="sec-card"))
    else:
        children.append(tree.text("No clauses were extracted from this contract.", prefix="sec-empty"))
    return tree.column(children, prefix="sec")


@functools.lru_cache(maxsize=256)
def _resolve_doc_name(doc_id: str) -> str:
    """Resolve a doc_id to a human filename. gs://-style ids use their tail;
    UUID ids are looked up in ``parsed_documents.originalFilename`` (fallback:
    ``sourceUrl`` tail, then a short id). Cached (filenames are stable) and
    fail-safe — a Firestore hiccup falls back to the id, never raises. The name
    is only shown in the authed workbench (same access gate as the doc)."""
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return "Document"
    if "/" in doc_id:  # gs:// URL or path — the tail is the filename already
        return doc_id.rsplit("/", 1)[-1] or doc_id
    try:
        from db.firestore import get_document

        doc = get_document("parsed_documents", doc_id) or {}
        name = doc.get("originalFilename") or (doc.get("sourceUrl") or "").rsplit("/", 1)[-1]
        if name:
            return str(name)
    except Exception as exc:
        logger.warning("a2ui: doc name resolve failed for %s: %s", doc_id, exc)
    return doc_id[:12]


def _doc_name(clauses: dict[str, Any], index: int) -> str:
    """Full resolved filename for a document's clause section."""
    return _resolve_doc_name(str(clauses.get("doc_id") or "").strip()) or f"Document {index + 1}"


def _tab_title(name: str, index: int) -> str:
    """Truncate a filename for a tab strip (keeps tabs short)."""
    return (name[:24] + "…") if len(name) > 25 else (name or f"Document {index + 1}")


# NOTE (7.5): the previous `_gather_extractions` accumulation hack (read all
# app:emitted:ppa_clauses:* from state → one tabbed surface) is gone — each
# extraction is now its own artifact surface (ppa_clauses:{doc_id}) and the
# workbench shows a tab per document. Routing/identity is a platform capability
# (register surface=...), not per-transform state-reading.


# --- diff rendering (Key Differences tab) ------------------------------------


def _diff_card(tree: _Tree, diff: dict[str, Any]) -> str:
    """One ClauseDifference → a Card. Built once; referenced from the All tab and
    its severity tab (v0.9 allows a shared child ref)."""
    name = str(diff.get("display_name") or diff.get("clause_name") or "Clause")
    severity = str(diff.get("severity") or "").strip()
    implication = str(diff.get("commercial_implication") or "").strip()
    left_val = diff.get("left_value")
    right_val = diff.get("right_value")

    rows = [tree.text(name, variant="h5", prefix="d-name")]
    if severity:
        rows.append(tree.text(f"Severity: {severity}", prefix="d-sev"))
    if implication:
        rows.append(tree.text(implication, prefix="d-impl"))
    rows.append(tree.text(f"Contract A: {left_val if left_val not in (None, '') else '—'}", prefix="d-left"))
    rows.append(tree.text(f"Contract B: {right_val if right_val not in (None, '') else '—'}", prefix="d-right"))
    # Interaction: "Explain this difference" sends a CHAT message. The generic
    # `chat:send` action carries a ready-built prompt; A2UISurfaceMount routes it
    # to the chat composer (sendMessage), so the agent's explanation lands in the
    # chat thread via the normal turn flow. (surface-action-run was wrong here —
    # its output only re-renders the surface, never chat text.)
    prompt = (
        f'Explain the "{name}" difference between the two PPAs in more detail. '
        f"Contract A: {left_val if left_val not in (None, '') else '—'}. "
        f"Contract B: {right_val if right_val not in (None, '') else '—'}. "
        "Why does this matter commercially, and which side does it favour?"
    )
    rows.append(
        tree.button(
            "Explain this difference",
            {"event": {"name": "chat:send", "context": {"prompt": prompt}}},
            variant="primary",
            prefix="d-explain",
        )
    )
    return tree.card(tree.column(rows, prefix="d-body"), prefix="d-card")


def _key_differences_section(tree: _Tree, differences: list[dict[str, Any]]) -> str:
    """The Key Differences tab body: summary + nested severity filter Tabs.

    Each diff row card is built once; the All tab lists every row and each
    severity tab lists only its rows (shared refs). Empty severities are omitted
    so the filter tabs stay meaningful.
    """
    material = sum(1 for d in differences if d.get("severity") == "material")
    summary = tree.text(
        f"{len(differences)} clause difference(s) · {material} material",
        variant="h4",
        prefix="kd-sum",
    )

    if not differences:
        empty = tree.text("The two contracts match on every clause analysed.", prefix="kd-empty")
        return tree.column([summary, empty], prefix="kd")

    # Build each row once; bucket ids by severity (unknown severities land in
    # their own bucket and simply don't get a filter tab — they stay in "All").
    all_ids: list[str] = []
    by_severity: dict[str, list[str]] = {sev: [] for sev in _SEVERITY_ORDER}
    for diff in differences:
        card_id = _diff_card(tree, diff)
        all_ids.append(card_id)
        by_severity.setdefault(str(diff.get("severity")), []).append(card_id)

    sev_tabs: list[tuple[str, str]] = [(f"All ({len(all_ids)})", tree.column(all_ids, prefix="sev-all"))]
    for sev in _SEVERITY_ORDER:
        ids = by_severity.get(sev) or []
        if ids:
            sev_tabs.append((f"{sev.capitalize()} ({len(ids)})", tree.column(ids, prefix=f"sev-{sev}")))

    severity_filter = tree.tabs(sev_tabs, prefix="sevtabs")
    return tree.column([summary, severity_filter], prefix="kd")


# --- registered transforms ---------------------------------------------------


def ppa_comparison_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform a typed ``PpaComparison`` result into A2UI v0.9 messages.

    Structure: Tabs [Key Differences · Contract A · Contract B]. Key Differences
    carries a nested severity-filter Tabs (All/Material/Moderate/Cosmetic). Each
    contract tab is its clause table with block_id citations. Returns ``None``
    for an error result (the model narrates the error in chat).
    """
    if _is_error(result):
        return None
    left = result.get("left")
    right = result.get("right")
    differences = result.get("differences") or []
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None

    tree = _Tree()
    left_name = _doc_name(left, 0)
    right_name = _doc_name(right, 1)
    kd = _key_differences_section(tree, [d for d in differences if isinstance(d, dict)])
    left_section = _clause_section(tree, left, heading="Contract A", subtitle=left_name)
    right_section = _clause_section(tree, right, heading="Contract B", subtitle=right_name)

    # Tab titles carry the filename (A/B role prefix kept so they still map to the
    # "Contract A:/Contract B:" labels in the Key Differences diff rows).
    main_tabs = tree.tabs(
        [
            ("Key Differences", kd),
            (f"A · {_tab_title(left_name, 0)}", left_section),
            (f"B · {_tab_title(right_name, 1)}", right_section),
        ],
        prefix="main",
    )
    title = tree.text("Contract Comparison", variant="h2", prefix="title")
    tree.root([title, main_tabs])
    return tree.messages()


def _clause_list(clauses: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured [{name, value, confidence}] in checklist order — stashed in the
    surface data model so the workbench ``ClausesArtefactTab`` renders a real,
    professional table (Basic-catalog A2UI can't; the component tree is the
    generic-render fallback). Mirrors the Sources tab pattern (6.11)."""
    out: list[dict[str, Any]] = []

    def _one(c: dict[str, Any]) -> dict[str, Any]:
        value = c.get("value")
        return {
            "name": str(c.get("display_name") or c.get("clause_name") or "Clause"),
            "value": "" if value in (None, "") else str(value),
            "confidence": str(c.get("confidence") or "").strip(),
        }

    for field in _STANDARD_CLAUSE_FIELDS:
        c = clauses.get(field)
        if isinstance(c, dict):
            out.append(_one(c))
    for c in clauses.get("other_clauses") or []:
        if isinstance(c, dict):
            out.append(_one(c))
    return out


def ppa_clauses_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform a typed ``PpaClauses`` result into an A2UI clause surface.

    Each extraction is its own workbench **artifact** (surface
    ``ppa_clauses:{doc_id}``, 7.5), so this renders just the ONE document's
    clauses — the workbench shows a tab per document (no in-transform
    accumulation). Returns ``None`` for an error result or one with no ``doc_id``.
    """
    if _is_error(result) or not result.get("doc_id"):
        return None
    name = _resolve_doc_name(str(result.get("doc_id") or ""))
    tree = _Tree()
    tree.root([_clause_section(tree, result, heading="Extracted Clauses", subtitle=name)])
    messages = tree.messages()
    # Stash structured clauses for the bespoke ClausesArtefactTab (retargeted to
    # the per-doc artifact surface by the registry, like the component tree).
    total = result.get("other_clauses_total") if result.get("other_clauses_truncated") else None
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": WORKSPACE_SURFACE_ID,
                "value": {"clauses": _clause_list(result), "docName": name, "truncatedTotal": total},
            },
        }
    )
    return messages


def _clauses_description(result: Any) -> str:
    """Short artifact description for a PpaClauses extraction (tab/index row)."""
    if not isinstance(result, dict):
        return ""
    n = sum(1 for f in _STANDARD_CLAUSE_FIELDS if isinstance(result.get(f), dict))
    n += len([c for c in (result.get("other_clauses") or []) if isinstance(c, dict)])
    return f"{n} clauses extracted"


def _comparison_description(result: Any) -> str:
    """Short artifact description for a PpaComparison (tab/index row)."""
    diffs = result.get("differences") or [] if isinstance(result, dict) else []
    material = sum(1 for d in diffs if isinstance(d, dict) and d.get("severity") == "material")
    return f"{len(diffs)} differences · {material} material"


def _clauses_surface(result: Any) -> str:
    """Per-document artifact surface id for an extraction (7.5)."""
    doc_id = result.get("doc_id") if isinstance(result, dict) else None
    return f"ppa_clauses:{doc_id}" if doc_id else WORKSPACE_SURFACE_ID


def _clauses_artifact(result: Any) -> dict[str, Any]:
    """Tab label = the tool/kind ("Clauses", NOT the filename — that's the
    Documents tab's job); the filename + count go in the tooltip (7.5)."""
    filename = _resolve_doc_name(str(result.get("doc_id") or "")) if isinstance(result, dict) else ""
    detail = _clauses_description(result)
    return {
        "kind": "clauses",
        "title": "Clauses",
        "description": " · ".join(p for p in (filename, detail) if p),
    }


# Compare → one stable "comparison" artifact tab; extract → one artifact tab per
# document (surface derived from doc_id). Tab title is the tool/kind; details
# (filename, counts) ride in `description` (the tab's hover tooltip). Multiple
# same-kind tabs are fine — surface ids stay distinct.
register(
    ppa_comparison_to_a2ui,
    tool_names=[COMPARE_TOOL],
    name="ppa_comparison",
    surface="ppa_comparison",
    artifact_meta=lambda r: {
        "kind": "comparison",
        "title": "Comparison",
        "description": _comparison_description(r),
    },
)
register(
    ppa_clauses_to_a2ui,
    tool_names=[EXTRACT_TOOL],
    name="ppa_clauses",
    surface=_clauses_surface,
    artifact_meta=_clauses_artifact,
)
