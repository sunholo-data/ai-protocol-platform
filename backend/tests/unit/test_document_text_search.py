"""Literal matching over parsed blocks without loading full documents into a model."""

from tools.documents.text_search import MAX_SNIPPET_CHARS, literal_pattern, matching_passages


def test_literal_punctuation_is_not_a_regex():
    blocks = [{"type": "paragraph", "text": "A+B is 9. aab is unrelated."}]
    assert matching_passages(blocks, literal_pattern(["a+b"]))[0]["matched"] == "A+B"
    assert matching_passages(blocks, literal_pattern([".*"])) == []


def test_deep_large_block_returns_multiple_small_windows():
    text = "z" * 150_000 + "curtailment 80 percent" + "z" * 150_000 + "replenish in 12 days" + "z" * 1000
    matches = matching_passages([{"type": "paragraph", "text": text}], literal_pattern(["curtailment", "replenish"]))
    assert len(matches) == 2 and "80 percent" in matches[0]["snippet"] and "12 days" in matches[1]["snippet"]
    assert all(len(m["snippet"]) <= MAX_SNIPPET_CHARS for m in matches)


def test_nested_table_rows_keep_page_and_block_reference():
    blocks = [
        {
            "type": "section",
            "page": 17,
            "children": [
                {
                    "type": "table",
                    "block_id": "prices",
                    "headers": [{"text": "Price"}],
                    "rows": [{"cells": [{"text": "EUR 63.40"}]}],
                }
            ],
        }
    ]
    hit = matching_passages(blocks, literal_pattern(["63.40"]))[0]
    assert hit["block_id"] == "prices" and hit["page"] == 17 and "EUR 63.40" in hit["snippet"]
