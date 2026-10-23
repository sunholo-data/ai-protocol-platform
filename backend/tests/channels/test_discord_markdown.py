"""Discord can't render Markdown tables — they're rewritten before posting."""

from __future__ import annotations

from channels._discord_markdown import to_discord_markdown

WIDE = """For last week you logged **27.0 hours**.



### Hours by Project
| Customer | Project | Hours |
| :--- | :--- | :--- |
| **Acme Retail A/S** | Retainer | 12.8 h |
| **Northwind Hotels Administration** | Analytics Implementation | 2.8 h |
| **Total** | | **27.0 h** |"""


def test_wide_table_becomes_one_bullet_per_row():
    out = to_discord_markdown(WIDE)
    assert "|" not in out
    assert "- **Acme Retail A/S** — Project: Retainer · Hours: 12.8 h" in out
    assert "- **Total** — Hours: **27.0 h**" in out  # empty cells are skipped


def test_blank_line_runs_collapse():
    assert "\n\n\n" not in to_discord_markdown(WIDE)


def test_narrow_table_becomes_an_aligned_code_block():
    out = to_discord_markdown("| Project | Hours |\n|---|---:|\n| Alpha | 12.8 h |\n| **Beta** | 3 h |\n\ndone")
    assert out.startswith("```\nProject   Hours\n")
    assert "Alpha    12.8 h\nBeta        3 h" in out  # numbers right-aligned, ** dropped
    assert out.endswith("```\n\ndone")


def test_code_fences_and_plain_text_are_untouched():
    fenced = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n\n\n\nx\n```"
    assert to_discord_markdown(fenced) == fenced
    plain = "Total **27.0 h** | not a table"
    assert to_discord_markdown(plain) == plain


def test_idempotent_for_streamed_edits():
    once = to_discord_markdown(WIDE)
    assert to_discord_markdown(once) == once
