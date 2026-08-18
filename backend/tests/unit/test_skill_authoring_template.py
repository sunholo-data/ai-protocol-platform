"""The Skill Studio authoring copilot template must stay well-formed.

v6.6.0 ONE-FORK-CONVERGENCE M3. The frontend AuthoringCopilot parses a fenced
```json proposals block whose `kind` values must match applyProposal.ts. This
test pins the contract from the backend side so the two can't silently drift.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_MD = (
    Path(__file__).resolve().parents[1].parent / "skills" / "templates" / "skill-authoring-assistant" / "SKILL.md"
)

# The canonical proposal kinds — must match frontend applyProposal.ts.
EXPECTED_KINDS = {
    "set_display_name",
    "set_description",
    "set_instructions",
    "set_model_tier",
    "add_sub_skill",
    "set_tools",
    "set_persona",
    "add_a2ui_surface",
    "set_welcome",
}


class TestAuthoringTemplate:
    def test_template_exists(self):
        assert _SKILL_MD.exists()

    def test_runs_smart_tier(self):
        body = _SKILL_MD.read_text()
        assert "model: smart" in body, "authoring is reasoning-heavy → smart tier"

    def test_gated_to_admin_tags(self):
        body = _SKILL_MD.read_text()
        assert "one-admin" in body
        assert "type: tagged" in body

    def test_declares_all_proposal_kinds(self):
        body = _SKILL_MD.read_text()
        for kind in EXPECTED_KINDS:
            assert kind in body, f"proposal kind {kind!r} missing from authoring instructions"

    def test_is_propose_only(self):
        body = _SKILL_MD.read_text().lower()
        assert "propose-only" in body
        assert "never" in body  # "never save / never claim you saved"


class TestSeed:
    def test_seed_loads_instructions(self):
        # The local fixture folds the template body into the seeded skill.
        from db.local_fixture import _SKILL_AUTHORING_INSTRUCTIONS

        assert "proposals" in _SKILL_AUTHORING_INSTRUCTIONS
        assert "set_model_tier" in _SKILL_AUTHORING_INSTRUCTIONS


class TestSystemTag:
    """The copilot is a platform-embedded system agent, not a pickable skill.

    The `system` tag is what hides it from the frontend skill switcher and the
    public marketplace (the studio mounts it directly by slug). Dropping the
    tag re-surfaces it in the dropdown for every admin — pin it on both the
    template (deployed seed source) and the local fixture.
    """

    def _frontmatter(self) -> dict:
        import yaml

        return yaml.safe_load(_SKILL_MD.read_text().split("---")[1])

    def test_template_is_system_tagged(self):
        assert "system" in (self._frontmatter().get("tags") or [])

    def test_local_fixture_is_system_tagged(self):
        from db.local_fixture import _demo_skills

        copilots = [s for s in _demo_skills(now=0.0) if s.get("slug") == "skill-authoring-assistant"]
        assert len(copilots) == 1
        assert "system" in copilots[0]["tags"]


class TestAuthoringTemplateAccess:
    """Who can reach the Skill Studio Copilot (2026-08-05).

    Opened to the whole ONE team on request. Safe because the copilot is
    propose-only — it drafts config for a human to review and saves nothing —
    so team access grants no write capability.
    """

    def _tags(self) -> list[str]:
        import yaml

        body = _SKILL_MD.read_text()
        front = body.split("---")[1]
        return yaml.safe_load(front)["access_control"]["tags"]

    def test_one_team_can_reach_it(self):
        assert "ONE" in self._tags(), (
            "the ONE team's derived group tag is 'ONE' (clients/acmeenergy.com"
            ".derived_group_tags) — without it the tag gate rejects the team"
        )

    def test_admins_keep_access(self):
        tags = self._tags()
        assert "aitana-admin" in tags
        assert "one-admin" in tags

    def test_stays_tag_gated_not_public(self):
        import yaml

        front = _SKILL_MD.read_text().split("---")[1]
        assert yaml.safe_load(front)["access_control"]["type"] == "tagged", (
            "widening to the team must not turn into an ungated skill"
        )
