"""Unit tests for backend/admin/platform_seed.py.

The seeder reads backend/skills/templates/*/SKILL.md, parses YAML
frontmatter + markdown body, and creates each as a platform-owned
public skill. Idempotent: skips any template whose `name` already exists
in Firestore.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from admin.platform_seed import (
    SeedSummary,
    _ensure_tool_permissions_wildcard,
    _parse_template,
    seed,
)
from db.models import SkillConfig


@pytest.fixture(autouse=True)
def _platform_owner_email(monkeypatch):
    """Provide PLATFORM_OWNER_EMAIL so seed() doesn't raise in non-LOCAL_MODE."""
    monkeypatch.setenv("PLATFORM_OWNER_EMAIL", "platform@test.com")


def _fake_template_dir(tmp_path, name: str, body: str = "Be helpful.", metadata: dict | None = None):
    md = metadata or {"model": "gemini-2.5-flash"}
    content = "---\n"
    content += f"name: {name}\n"
    content += "description: >\n  Do things.\n"
    content += "metadata:\n"
    for k, v in md.items():
        content += f"  {k}: {v}\n"
    content += "---\n\n"
    content += body + "\n"
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content)
    return tmp_path


def _make_config(name: str, **overrides) -> SkillConfig:
    defaults = {
        "name": name,
        "skillId": f"platform-{name}",
        "ownerId": "aitana-platform",
        "ownerEmail": "platform@yourcompany.com",
        "accessControl": {"type": "public"},
    }
    defaults.update(overrides)
    return SkillConfig(**defaults)


# === _parse_template ===


def test_parse_template_extracts_frontmatter_and_body(tmp_path):
    _fake_template_dir(tmp_path, "alpha", body="Help the user.")
    parsed = _parse_template(tmp_path / "alpha" / "SKILL.md")
    assert parsed["name"] == "alpha"
    assert "Help the user" in parsed["instructions"]
    assert parsed["metadata"]["model"] == "gemini-2.5-flash"


def test_parse_template_missing_frontmatter_raises(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("Just body, no frontmatter\n")
    with pytest.raises(ValueError, match="frontmatter"):
        _parse_template(bad / "SKILL.md")


def test_parse_template_extracts_shell_block(tmp_path):
    """v6.4.0 SHELL-MODES: a `shell` frontmatter block is parsed through."""
    skill_dir = tmp_path / "withshell"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: withshell\n"
        "description: >\n  Do things.\n"
        "metadata:\n  model: gemini-2.5-flash\n"
        "shell:\n  mode: doc-compare\n  chat:\n    default_state: minimised\n"
        "---\n\nBe helpful.\n"
    )
    parsed = _parse_template(skill_dir / "SKILL.md")
    assert parsed["shell"]["mode"] == "doc-compare"
    assert parsed["shell"]["chat"]["default_state"] == "minimised"


def test_parse_template_no_shell_is_none(tmp_path):
    """Chat-primary skills omit `shell`; it parses to None (ChatShell fallback)."""
    _fake_template_dir(tmp_path, "noshell")
    parsed = _parse_template(tmp_path / "noshell" / "SKILL.md")
    assert parsed.get("shell") is None


def test_one_doc_compare_template_is_chat_primary():
    """v6.6.0 ONE-FORK-CONVERGENCE: one-doc-compare was standardised onto the
    chat-primary ChatShell — its `doc-compare` shell block was retired from the
    production path (the specialised shell read as "a different app"). The
    side-by-side comparison still renders to the A2UI `workspace` surface, which
    ChatShell shows in its workbench. So the template now declares NO shell."""
    from admin.platform_seed import DEFAULT_TEMPLATES_ROOT

    skill_md = DEFAULT_TEMPLATES_ROOT / "one-doc-compare" / "SKILL.md"
    if not skill_md.exists():
        pytest.skip("one-doc-compare template not present")
    parsed = _parse_template(skill_md)
    assert parsed["shell"] is None, "one-doc-compare should no longer declare a specialised shell"
    # shell=None → ShellRouter falls back to ChatShell.
    cfg = SkillConfig(name="one-doc-compare", description=parsed["description"], shell=parsed["shell"])
    assert cfg.shell is None


def test_one_ppa_expert_template_has_no_shell():
    """Backwards-compat: the chat-primary one-ppa-expert skill declares no
    shell block, so it parses to shell=None and renders ChatShell."""
    from admin.platform_seed import DEFAULT_TEMPLATES_ROOT

    skill_md = DEFAULT_TEMPLATES_ROOT / "one-ppa-expert" / "SKILL.md"
    if not skill_md.exists():
        pytest.skip("one-ppa-expert template not present")
    parsed = _parse_template(skill_md)
    assert parsed.get("shell") is None


# === seed() ===


def test_seed_empty_firestore_creates_all(tmp_path):
    """First run: no existing platform skills → create one for each template."""
    _fake_template_dir(tmp_path, "alpha")
    _fake_template_dir(tmp_path, "beta")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_list.return_value = []  # no existing platform skills
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    assert summary.created == 2
    assert summary.skipped == 0
    assert summary.failed == []
    # Verify each create call sets the right owner + access
    for call in mock_create.call_args_list:
        kwargs = call.kwargs
        assert kwargs["owner_id"] == "aitana-platform"
        assert kwargs["owner_email"]  # non-empty — value comes from PLATFORM_OWNER_EMAIL env
        assert kwargs["accessControl"] == {"type": "public"}


def test_seed_idempotent_refreshes_existing_instead_of_recreating(tmp_path):
    """G16 (template-fork-ergonomics.md): when a skill already exists,
    the seeder REFRESHES its template fields (description / instructions
    / metadata) on Firestore rather than just skipping. The pre-G16
    behaviour silently dropped SKILL.md edits until someone deleted the
    row by hand; this test pins the refresh contract so the regression
    can't reappear.
    """
    _fake_template_dir(tmp_path, "alpha")
    _fake_template_dir(tmp_path, "beta")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
        patch("admin.platform_seed.skill_config.update_skill") as mock_update,
    ):
        mock_list.return_value = [_make_config("alpha"), _make_config("beta")]
        mock_update.return_value = None  # return value unused
        summary = seed(templates_root=tmp_path)

    assert summary.created == 0
    assert summary.refreshed == 2  # the contract change
    assert summary.skipped == 0
    assert summary.failed == []
    mock_create.assert_not_called()
    assert mock_update.call_count == 2
    # Each refresh pushes the parsed-from-disk template fields.
    for call in mock_update.call_args_list:
        _skill_id, updates = call.args
        assert "description" in updates
        assert "instructions" in updates
        assert "skillMetadata" in updates


def test_seed_preserves_durable_firestore_skills(tmp_path):
    """v6.9.0 9.2: a skill created/edited durably in-product (managed_by=
    'firestore') is NOT clobbered by a template re-seed — the drift-protection
    regression guard. A template-provenance skill (managed_by None) is still
    refreshed from disk."""
    _fake_template_dir(tmp_path, "alpha")
    _fake_template_dir(tmp_path, "beta")

    durable = _make_config("alpha", managedBy="firestore")
    template = _make_config("beta")  # managed_by None → clobberable

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
        patch("admin.platform_seed.skill_config.update_skill") as mock_update,
    ):
        mock_list.return_value = [durable, template]
        mock_update.return_value = None
        summary = seed(templates_root=tmp_path)

    assert summary.preserved == 1  # alpha (durable) left untouched
    assert summary.refreshed == 1  # beta (template) refreshed from disk
    mock_create.assert_not_called()
    updated_ids = [call.args[0] for call in mock_update.call_args_list]
    assert durable.skill_id not in updated_ids  # never clobbered
    assert template.skill_id in updated_ids
    # The refresh stamps template provenance.
    beta_updates = next(c.args[1] for c in mock_update.call_args_list if c.args[0] == template.skill_id)
    assert beta_updates.get("managedBy") == "template"


def test_seed_refresh_backfills_missing_slug(tmp_path):
    """A skill created before slug-at-create existed has slug=None; refresh must
    backfill it, else skillHref falls back to the bare-id `/chat/{id}` URL that
    the frontend slug-resolver rejected → skill unloadable."""
    _fake_template_dir(tmp_path, "alpha")
    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill"),
        patch("admin.platform_seed.skill_config.update_skill") as mock_update,
        patch("admin.platform_seed.unique_slug", return_value="alpha"),
    ):
        mock_list.return_value = [_make_config("alpha", slug=None)]
        seed(templates_root=tmp_path)

    _skill_id, updates = mock_update.call_args_list[0].args
    assert updates.get("slug") == "alpha"


def test_seed_refresh_preserves_existing_slug(tmp_path):
    """Refresh must NOT churn a skill that already has a slug."""
    _fake_template_dir(tmp_path, "alpha")
    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill"),
        patch("admin.platform_seed.skill_config.update_skill") as mock_update,
        patch("admin.platform_seed.unique_slug", return_value="SHOULD-NOT-BE-USED"),
    ):
        mock_list.return_value = [_make_config("alpha", slug="alpha-custom")]
        seed(templates_root=tmp_path)

    _skill_id, updates = mock_update.call_args_list[0].args
    assert "slug" not in updates


def test_seed_dry_run_reports_changes_but_writes_nothing(tmp_path):
    """dry_run does every read + validation and reports what WOULD change, but
    issues zero Firestore writes — so a re-seed can be previewed against a shared
    env (e.g. prod) before applying. This is the CLI's --dry-run path."""
    _fake_template_dir(tmp_path, "alpha")  # exists → would refresh
    _fake_template_dir(tmp_path, "beta")  # new → would create
    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[_make_config("alpha")]),
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
        patch("admin.platform_seed.skill_config.update_skill") as mock_update,
        patch("admin.platform_seed.skill_config.delete_skill") as mock_delete,
        patch("admin.platform_seed.fs.get_document", return_value=None),
        patch("admin.platform_seed.fs.set_document") as mock_set,
        patch("admin.platform_seed.unique_slug", return_value="slug"),
    ):
        summary = seed(templates_root=tmp_path, dry_run=True)

    # Counts reflect what WOULD happen…
    assert summary.created == 1
    assert summary.refreshed == 1
    assert summary.tool_permissions_wildcard_seeded is True
    # …but NOTHING was written.
    mock_create.assert_not_called()
    mock_update.assert_not_called()
    mock_delete.assert_not_called()
    mock_set.assert_not_called()


def test_seed_malformed_template_is_failed_not_raise(tmp_path):
    """One bad template should not abort the whole run."""
    _fake_template_dir(tmp_path, "alpha")  # valid
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here\n")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_list.return_value = []
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    assert summary.created == 1
    assert summary.skipped == 0
    assert "broken" in summary.failed


def test_seed_summary_is_a_dataclass():
    s = SeedSummary(created=3, skipped=2, failed=["x"])
    assert s.created == 3
    assert s.skipped == 2
    assert s.failed == ["x"]


# === _ensure_tool_permissions_wildcard ===


def test_wildcard_seed_creates_doc_when_absent():
    """First run: no wildcard doc exists → create it, return True."""
    with (
        patch("admin.platform_seed.fs.get_document", return_value=None) as mock_get,
        patch("admin.platform_seed.fs.set_document") as mock_set,
    ):
        result = _ensure_tool_permissions_wildcard()

    assert result is True
    mock_get.assert_called_once_with("tool_permissions", "*")
    mock_set.assert_called_once()
    _, args_doc_id, payload = mock_set.call_args.args
    assert args_doc_id == "*"
    assert payload["tools"] == ["*"]
    assert payload["denied"] == []
    assert payload["type"] == "wildcard"


def test_wildcard_seed_idempotent_skips_when_present():
    """Second run: wildcard doc already exists → skip, return False."""
    existing = {"type": "wildcard", "tools": ["*"], "denied": [], "created_by": "platform_seed"}
    with (
        patch("admin.platform_seed.fs.get_document", return_value=existing),
        patch("admin.platform_seed.fs.set_document") as mock_set,
    ):
        result = _ensure_tool_permissions_wildcard()

    assert result is False
    mock_set.assert_not_called()


def test_seed_summary_includes_wildcard_flag(tmp_path):
    """seed() surfaces the wildcard-seeded flag in its summary."""
    _fake_template_dir(tmp_path, "alpha")

    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[]),
        patch("admin.platform_seed.skill_config.create_skill", side_effect=lambda **kw: _make_config(name=kw["name"])),
        patch("admin.platform_seed.fs.get_document", return_value=None),
        patch("admin.platform_seed.fs.set_document"),
    ):
        summary = seed(templates_root=tmp_path)

    assert summary.tool_permissions_wildcard_seeded is True
    d = summary.as_dict()
    assert "tool_permissions_wildcard_seeded" in d


def test_seed_raises_when_platform_owner_email_unset(tmp_path, monkeypatch):
    """seed() must raise RuntimeError (not silently use Aitana email) when
    PLATFORM_OWNER_EMAIL is unset in non-LOCAL_MODE — item #3 of the template
    fork-ergonomics upstream feedback."""
    monkeypatch.delenv("PLATFORM_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("LOCAL_MODE", raising=False)

    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[]),
        patch("admin.platform_seed.fs.get_document", return_value={"type": "wildcard"}),
    ):
        with pytest.raises(RuntimeError, match="PLATFORM_OWNER_EMAIL"):
            seed(templates_root=tmp_path)


def test_seed_sets_slug_at_creation(tmp_path):
    """Each newly seeded skill must have a slug — otherwise the friendly
    URL /chat/@aitana-platform/{slug} 404s and we have to backfill in every
    fresh environment. Regression for the bug where test/prod were cut
    without slugs and the marketplace links broke."""
    _fake_template_dir(tmp_path, "general-assistant")
    _fake_template_dir(tmp_path, "code-assistant")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
        patch("admin.platform_seed.unique_slug", side_effect=lambda _o, base, **_: base),
    ):
        mock_list.return_value = []
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    assert summary.created == 2
    slugs = {call.kwargs["slug"] for call in mock_create.call_args_list}
    assert slugs == {"general-assistant", "code-assistant"}


# === G17 demo-skill opt-in flag ===


def test_seed_skips_demo_skills_when_include_demos_false(tmp_path, monkeypatch):
    """G17 (template-fork-ergonomics.md): a fork that sets
    ``_INCLUDE_DEMO_SKILLS=false`` gets a clean slate — none of the seven
    inherited workshop demos are seeded. Custom (non-demo) templates the
    fork has added still seed normally.
    """
    monkeypatch.setenv("_INCLUDE_DEMO_SKILLS", "false")

    # Three demo skills the fork inherits and doesn't want
    _fake_template_dir(tmp_path, "code-assistant")
    _fake_template_dir(tmp_path, "data-extractor")
    _fake_template_dir(tmp_path, "workspace-demo")
    # One custom skill the fork added itself — must still seed
    _fake_template_dir(tmp_path, "my-custom-fork-skill")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_list.return_value = []
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    # Only the custom skill was created. Demos were skipped, not failed.
    assert summary.created == 1
    assert summary.skipped == 3
    assert summary.failed == []
    created_names = {call.kwargs["name"] for call in mock_create.call_args_list}
    assert created_names == {"my-custom-fork-skill"}


def test_seed_includes_demo_skills_when_include_demos_true(tmp_path, monkeypatch):
    """Inverse: explicit opt-in seeds every demo + custom skill."""
    monkeypatch.setenv("_INCLUDE_DEMO_SKILLS", "true")

    _fake_template_dir(tmp_path, "code-assistant")
    _fake_template_dir(tmp_path, "workspace-demo")
    _fake_template_dir(tmp_path, "my-custom-fork-skill")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_list.return_value = []
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    assert summary.created == 3
    assert summary.skipped == 0


def test_seed_default_includes_demos_for_backwards_compat(tmp_path, monkeypatch):
    """Unset env var defaults to 'include demos' (platform repo's own
    dev/test/prod deploys depend on the workshop demos being seeded).
    """
    monkeypatch.delenv("_INCLUDE_DEMO_SKILLS", raising=False)

    _fake_template_dir(tmp_path, "code-assistant")

    with (
        patch("admin.platform_seed.skill_config.list_skills") as mock_list,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_list.return_value = []
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])

        summary = seed(templates_root=tmp_path)

    assert summary.created == 1  # demo was included
    assert summary.skipped == 0


def test_demo_skill_names_constant_matches_shipped_templates_directory():
    """Sanity check: the hardcoded DEMO_SKILL_NAMES constant matches the
    actual directory inventory at backend/skills/templates/. If a demo is
    added or renamed without updating DEMO_SKILL_NAMES, the gating
    silently fails for that one skill — this test catches it.
    """
    from admin.platform_seed import DEFAULT_TEMPLATES_ROOT, DEMO_SKILL_NAMES

    if not DEFAULT_TEMPLATES_ROOT.exists():
        pytest.skip("templates directory not present in test env")

    on_disk = {c.name for c in DEFAULT_TEMPLATES_ROOT.iterdir() if c.is_dir() and (c / "SKILL.md").exists()}
    # All seven demo names must currently exist on disk.
    missing_on_disk = DEMO_SKILL_NAMES - on_disk
    assert not missing_on_disk, (
        f"DEMO_SKILL_NAMES lists {missing_on_disk!r} but they don't exist "
        f"in backend/skills/templates/. Either restore the directory or "
        f"remove the entry from DEMO_SKILL_NAMES."
    )
    # No fork-added skill should accidentally land in DEMO_SKILL_NAMES.
    # (If a non-demo template gets added later it must NOT be marked demo.)
    # This branch is a soft check — only assert when on_disk is non-empty.
    # We don't assert the inverse (every on_disk dir is in DEMO_SKILL_NAMES)
    # because forks may add custom non-demo templates.


# === G16 idempotent seeder: purge stale-owner skills ===


def test_seed_purges_skills_owned_by_previous_uids(tmp_path, monkeypatch):
    """G16: a fork that rotates PLATFORM_OWNER_UID can declare the old
    UIDs via PLATFORM_PREVIOUS_OWNER_UIDS (comma-separated) and the
    seeder purges any skills still owned by them on the next deploy.
    Without this, rotating PLATFORM_OWNER_UID strands the old owner's
    skills in Firestore until manual cleanup.
    """
    monkeypatch.setenv("PLATFORM_PREVIOUS_OWNER_UIDS", "old-uid-1,old-uid-2")

    stale_a = _make_config("stale-from-old-uid-1")
    stale_b = _make_config("stale-from-old-uid-2")
    current = _make_config("alpha")  # currently-owned, should survive

    _fake_template_dir(tmp_path, "alpha")

    # Different return per owner_id call.
    def list_skills_by_owner(owner_id=None, **_):
        if owner_id == "old-uid-1":
            return [stale_a]
        if owner_id == "old-uid-2":
            return [stale_b]
        # PLATFORM_OWNER_UID — the current-owner read
        return [current]

    with (
        patch(
            "admin.platform_seed.skill_config.list_skills",
            side_effect=list_skills_by_owner,
        ),
        patch("admin.platform_seed.skill_config.delete_skill", return_value=True) as mock_delete,
        patch("admin.platform_seed.skill_config.update_skill", return_value=None),
        patch("admin.platform_seed.skill_config.create_skill"),
    ):
        summary = seed(templates_root=tmp_path)

    assert summary.purged == 2
    # Two delete_skill calls — one per stale row.
    assert mock_delete.call_count == 2
    deleted_ids = {call.args[0] for call in mock_delete.call_args_list}
    assert deleted_ids == {stale_a.skill_id, stale_b.skill_id}


def test_seed_purge_phase_is_noop_when_env_var_unset(tmp_path, monkeypatch):
    """No PLATFORM_PREVIOUS_OWNER_UIDS = nothing to purge — most forks."""
    monkeypatch.delenv("PLATFORM_PREVIOUS_OWNER_UIDS", raising=False)

    _fake_template_dir(tmp_path, "alpha")

    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[]),
        patch("admin.platform_seed.skill_config.delete_skill") as mock_delete,
        patch("admin.platform_seed.skill_config.create_skill") as mock_create,
    ):
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])
        summary = seed(templates_root=tmp_path)

    assert summary.purged == 0
    mock_delete.assert_not_called()


def test_previous_owner_uids_parses_comma_separated_with_whitespace(monkeypatch):
    """The env-var parser tolerates extra whitespace and empty entries."""
    from admin.platform_seed import _previous_owner_uids

    monkeypatch.setenv("PLATFORM_PREVIOUS_OWNER_UIDS", " uid-a , uid-b ,, uid-c ")
    assert _previous_owner_uids() == ["uid-a", "uid-b", "uid-c"]

    monkeypatch.setenv("PLATFORM_PREVIOUS_OWNER_UIDS", "")
    assert _previous_owner_uids() == []

    monkeypatch.delenv("PLATFORM_PREVIOUS_OWNER_UIDS")
    assert _previous_owner_uids() == []


def test_seed_summary_includes_refreshed_and_purged_counts():
    """The SeedSummary dataclass exposes both new G16 counters in as_dict."""
    s = SeedSummary(created=1, refreshed=5, purged=2)
    d = s.as_dict()
    assert d["created"] == 1
    assert d["refreshed"] == 5
    assert d["purged"] == 2
