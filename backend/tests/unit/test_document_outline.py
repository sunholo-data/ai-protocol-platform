"""Deterministic structure of a parsed document: outline, sections, citations."""

from unittest.mock import patch

from tools.documents import outline
from tools.documents.context import UNCHECKED_INTERNAL, build_document_context
from tools.documents.outline import build_structure, render_outline, section_blocks, select_sections
from tools.documents.text_search import literal_pattern, matching_passages


def h(text, level=1):
    return {"type": "heading", "text": text, "level": level}


def p(text):
    return {"type": "text", "text": text}


def page(label=""):
    return {"type": "section", "kind": "page-break", **({"name": label} if label else {})}


# The shape of an EDGAR filing as AILANG Parse returns it: a page marker named
# by the printed page number opens each page, headings are flat blocks.
FILING = [
    page("i"),
    p("Cover page"),
    page("1"),
    h("Prospectus Summary"),
    p("We are an AI company."),
    h("Overview", 2),
    p("Overview of the business."),
    page("2"),
    h("Risk Factors"),
    h("Overview", 2),
    p("Risks overview."),
    h("Risks Related to Regulation", 2),
    page("3"),
    h("Export controls", 3),
    p("Export controls may restrict sales."),
    {"type": "section", "kind": "comment", "children": [p("reviewer: check this")]},
    page(),  # a hard break with no printed number is not a page
    h("Use of Proceeds"),
    p("General corporate purposes."),
]


def titles(nodes):
    return [(n.id, n.title) for n in nodes]


def test_outline_nests_headings_and_numbers_them():
    s = build_structure(FILING)
    assert titles(s.nodes) == [
        ("1", "Prospectus Summary"),
        ("1.1", "Overview"),
        ("2", "Risk Factors"),
        ("2.1", "Overview"),
        ("2.2", "Risks Related to Regulation"),
        ("2.2.1", "Export controls"),
        ("3", "Use of Proceeds"),
    ]
    assert s.front_matter_chars == len("Cover page")


def test_page_ranges_come_from_printed_page_markers():
    s = build_structure(FILING)
    assert (s.find("1").page_start, s.find("1").page_end) == ("1", "1")
    assert (s.find("2").page_start, s.find("2").page_end) == ("2", "3")
    # The unnamed break did not invent a page 4.
    assert s.find("3").page_start == "3"
    assert outline.page_range(s.find("2")) == "pp. 2\u20133"


def test_section_includes_subsections_and_stops_at_a_sibling():
    s = build_structure(FILING)
    text = " ".join(b.get("text", "") for b in section_blocks(s, [s.find("2")]))
    assert "Export controls may restrict sales." in text
    assert "We are an AI company." not in text and "General corporate purposes." not in text


def test_comments_are_not_document_text():
    s = build_structure(FILING)
    assert all("reviewer" not in leaf.text for leaf in s.leaves)


def test_level_skips_still_nest():
    s = build_structure([h("Part I"), h("Item 1", 3), p("x"), h("Item 2", 3), h("Part II")])
    assert titles(s.nodes) == [("1", "Part I"), ("1.1", "Item 1"), ("1.2", "Item 2"), ("2", "Part II")]


def test_named_containers_are_sections_and_a_split_sheet_is_one_sheet():
    table = {"type": "table", "headers": [{"text": "Month"}], "rows": [{"cells": [{"text": "Jan"}]}]}
    blocks = [
        {"type": "section", "kind": "sheet", "name": "Q1", "children": [h("Volumes", 2), table]},
        # block_store splits an oversized sheet into consecutive same-named sections.
        {"type": "section", "kind": "sheet", "name": "Q1", "children": [table]},
        {"type": "section", "kind": "sheet", "name": "Q2", "children": [table]},
    ]
    s = build_structure(blocks)
    assert titles(s.nodes) == [("1", "Q1"), ("1.1", "Volumes"), ("2", "Q2")]
    assert len(section_blocks(s, [s.find("1")])) == 3
    assert outline.describe(s.find("2")).startswith("2 Q2 (sheet)")


def test_select_by_ids_and_titles():
    s = build_structure(FILING)
    assert titles(select_sections(s, "2.2, 3").nodes) == [
        ("2.2", "Risks Related to Regulation"),
        ("3", "Use of Proceeds"),
    ]
    assert titles(select_sections(s, "risk factors").nodes) == [("2", "Risk Factors")]
    assert titles(select_sections(s, "export").nodes) == [("2.2.1", "Export controls")]
    missing = select_sections(s, "2.9")
    assert not missing.nodes and "2.9" in missing.problem


def test_ambiguous_title_lists_candidates_instead_of_guessing():
    selection = select_sections(build_structure(FILING), "Overview")
    assert not selection.nodes
    assert "2 sections match" in selection.problem
    assert "1.1 Overview" in selection.problem and "in: Prospectus Summary" in selection.problem
    assert "2.1 Overview" in selection.problem and "in: Risk Factors" in selection.problem


def test_overlapping_selection_returns_each_block_once():
    s = build_structure(FILING)
    assert section_blocks(s, [s.find("2"), s.find("2.2.1")]) == section_blocks(s, [s.find("2")])


def test_long_outline_lowers_its_depth_and_counts_hidden_subsections(monkeypatch):
    monkeypatch.setattr(outline, "MAX_OUTLINE_LINES", 4)
    s = build_structure(FILING)
    lines, depth = render_outline(s, max_depth=3)
    assert depth == 1 and len(lines) == 3
    assert lines[1].startswith("- 2 Risk Factors") and lines[1].endswith("+3 subsections")


def _record(blocks):
    return {"originalFilename": "s1.htm", "blocks": blocks, "parseStatus": "parsed", "metadata": {}}


def _context(blocks, **kwargs):
    with patch("tools.documents.context.get_document", return_value=_record(blocks)):
        return build_document_context("doc1", caller_uid=UNCHECKED_INTERNAL, **kwargs)


def test_outline_mode_lists_sections_without_their_text():
    content, blocks = _context(FILING, mode="outline")
    assert blocks is None
    assert "- 2 Risk Factors · pp. 2\u20133" in content
    assert "    - 2.2.1 Export controls · p. 3" in content
    assert "Export controls may restrict sales." not in content
    assert 'section="<id>"' in content


def test_outline_of_one_section():
    content, _ = _context(FILING, mode="outline", section="2")
    assert "Outline of section 2 Risk Factors" in content
    assert "- 2.2 Risks Related to Regulation" in content
    assert "Prospectus Summary" not in content


def test_outline_of_a_document_without_headings_says_so():
    content, _ = _context([p("Just one paragraph.")], mode="outline")
    assert "no outline" in content


def test_section_read_names_its_place_and_pages():
    content, _ = _context(FILING, section="2.2")
    assert "**Section 2.2 Risks Related to Regulation · pp. 2\u20133" in content
    assert "in: Risk Factors" in content
    assert "Export controls may restrict sales." in content


def test_ambiguous_section_read_returns_the_choice_not_a_document():
    content, blocks = _context(FILING, section="Overview", mode="blocks")
    assert blocks is None and "2 sections match" in content and '"blocks"' not in content


def test_grep_hits_carry_section_heading_path_and_printed_page():
    hit = matching_passages(FILING, literal_pattern(["export controls may"]))[0]
    assert hit["section"] == "2.2.1"
    assert hit["heading_path"] == "Risk Factors \u203a Risks Related to Regulation \u203a Export controls"
    assert hit["page"] == "3"


def test_grep_card_shows_heading_path_and_page():
    from adk.a2ui_project_search_render import project_text_matches_to_a2ui

    row = {"filename": "s1.htm", "block_id": "14", "snippet": "…", "page": "3", "heading_path": "Risk Factors"}
    messages = project_text_matches_to_a2ui({"results": [row]})
    sources = messages[-1]["updateDataModel"]["value"]["sources"]
    assert sources[0]["title"] == "s1.htm — Risk Factors, page 3"


def test_a_retrieved_chunk_is_located_by_its_words_not_its_formatting():
    s = build_structure(FILING)
    chunk = "Risk Factors > Risks Related to Regulation > Export controls\nExport controls may restrict sales."
    leaf = outline.locate_passage(s, chunk)
    assert leaf is not None and leaf.node.id == "2.2.1" and leaf.page == "3"


def test_repeated_boilerplate_is_not_located():
    s = build_structure(
        [
            h("A"),
            p("This clause applies to all parties equally."),
            h("B"),
            p("This clause applies to all parties equally."),
        ]
    )
    assert outline.locate_passage(s, "This clause applies to all parties equally.") is None
