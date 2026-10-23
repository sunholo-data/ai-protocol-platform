"""guide verify: a bolded phrase passes if the guide shows it, with numbers as placeholders."""

from aiplatform.commands.guide import _norm, judge

GUIDE = _norm(
    "The badge reads **Searchable · 2**. A file goes into a folder such as `Uploads 2026-10-16`, "
    "marked *(this chat)*. Click **Make searchable**."
)
TURN = {"errors": [], "tools": [("knowledge_search", "{}")]}


def _reply(text: str) -> dict:
    return {**TURN, "reply": text + " See /help/folders#x."}


def test_examples_and_placeholders_are_not_invented():
    for bold in ("Searchable · [number]", "Searchable · 7", "Uploads 2026-09-04", "(this chat)", "Make searchable"):
        assert judge("q", "folders#x", _reply(f"Use **{bold}**"), GUIDE) == "", bold


def test_a_made_up_button_still_fails():
    assert "labels not in the guide" in judge("q", "folders#x", _reply("Click **Share folder**"), GUIDE)


def test_either_listed_section_passes():
    expected = "getting-started#pick-a-skill|skills#switch-to-another-skill"
    reply = {**TURN, "reply": "See /help/skills#switch-to-another-skill."}
    assert judge("q", expected, reply, GUIDE) == ""
    miss = {**TURN, "reply": "See /help/skills#create-a-new-skill."}
    assert "expected a link to" in judge("q", expected, miss, GUIDE)


def test_every_expected_section_exists_in_the_guide():
    """A renamed heading must fail here, not as a mystery miss in a live run."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    docs = root / "docs" / "user-guide"

    def slugify(text: str) -> str:  # same rule as scripts/user-guide/bundle.mjs
        text = re.sub(r"[*_`]", "", text.lower())
        text = re.sub(r"[^\w\s-]", "", text).strip()
        return re.sub(r"\s", "-", text)

    anchors = {
        f"{md.stem}#{slugify(m.group(1))}"
        for md in docs.glob("*.md")
        for m in re.finditer(r"^#{2,3}\s+(.+?)\s*$", md.read_text(), re.M)
    }
    rows = [
        ln.split("\t", 1)[0]
        for ln in (root / "scripts" / "user-guide" / "questions.tsv").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    missing = [alt for exp in rows if exp != "-" for alt in exp.split("|") if alt not in anchors]
    assert not missing, f"questions.tsv expects sections the guide doesn't have: {missing}"
