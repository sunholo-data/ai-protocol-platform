"""Skill refs accept a friendly slug, not just the canonical doc id.

Regression (2026-08-05): `POST /api/skill/skill-authoring-assistant/stream`
returned 404 `{"detail":"Skill not found"}` on test while the identical UI
worked for one-assistant. The frontend holds whichever identifier it happened
to resolve — a UUID for one, the slug for the other — and the route did a raw
`get_skill(ref)` doc-id lookup, so the slug matched nothing.

This is the recurring class CLAUDE.md #9 names: the DEPLOYED doc-id is a UUID
while the local fixture uses slug-as-doc-id, so a slug caller passes locally
and 404s deployed. The rule is: accept aliases on input, resolve friendly→id,
and normalize to the canonical id at the boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _skill(skill_id: str, slug: str, owner: str):
    s = MagicMock()
    s.skill_id = skill_id
    s.slug = slug
    s.owner_id = owner
    return s


class TestResolveSkillRef:
    def test_canonical_id_resolves_directly(self):
        from skills import skill_config

        want = _skill("b153a8d3-uuid", "skill-authoring-assistant", "aitana-platform")
        with patch.object(skill_config, "get_skill", return_value=want) as g:
            got = skill_config.resolve_skill_ref("b153a8d3-uuid", caller_uid="u1")

        assert got is want
        g.assert_called_once_with("b153a8d3-uuid")

    def test_slug_resolves_via_the_platform_namespace(self):
        """The exact failing case: a platform skill addressed by slug."""
        from skills import skill_config

        want = _skill("b153a8d3-uuid", "skill-authoring-assistant", "aitana-platform")

        def by_slug(owner, slug):
            return want if owner == "aitana-platform" and slug == "skill-authoring-assistant" else None

        with (
            patch.object(skill_config, "get_skill", return_value=None),
            patch.object(skill_config, "find_by_slug", side_effect=by_slug),
        ):
            got = skill_config.resolve_skill_ref("skill-authoring-assistant", caller_uid="u1")

        assert got is want

    def test_callers_own_namespace_wins_over_platform(self):
        from skills import skill_config

        mine = _skill("mine", "helper", "u1")
        platform = _skill("theirs", "helper", "aitana-platform")

        def by_slug(owner, slug):
            return {"u1": mine, "aitana-platform": platform}.get(owner)

        with (
            patch.object(skill_config, "get_skill", return_value=None),
            patch.object(skill_config, "find_by_slug", side_effect=by_slug),
        ):
            got = skill_config.resolve_skill_ref("helper", caller_uid="u1")

        assert got is mine

    def test_unknown_ref_returns_none(self):
        from skills import skill_config

        with (
            patch.object(skill_config, "get_skill", return_value=None),
            patch.object(skill_config, "find_by_slug", return_value=None),
        ):
            assert skill_config.resolve_skill_ref("nope", caller_uid="u1") is None

    def test_slug_lookup_failure_does_not_mask_the_404(self):
        from skills import skill_config

        with (
            patch.object(skill_config, "get_skill", return_value=None),
            patch.object(skill_config, "find_by_slug", side_effect=RuntimeError("firestore down")),
        ):
            assert skill_config.resolve_skill_ref("anything", caller_uid="u1") is None


class TestStreamPathUsesTheResolver:
    """The stream entrypoint must go through the alias-aware resolver, and must
    normalize to the canonical id before anything downstream keys off it."""

    def _run(self, resolved, can_access=True):
        import asyncio

        from auth.firebase_auth import User
        from skills import skill_processor

        access = MagicMock()
        access.can_access_skill.return_value = can_access
        user = User(uid="u1", email="u1@example.com", domain="example.com")

        captured: dict = {}

        def fake_ensure(thread_id, skill_id, *a, **kw):
            captured["skill_id"] = skill_id
            raise _Stop()

        class _Stop(Exception):
            pass

        with (
            patch.object(skill_processor, "get_skill", return_value=None),
            patch.object(skill_processor, "resolve_skill_ref", return_value=resolved) as resolver,
            patch.object(skill_processor, "_ensure_session_index", side_effect=fake_ensure),
            patch.object(skill_processor, "record_shell_mode"),
        ):
            gen = skill_processor.process_skill_request(
                skill_id="skill-authoring-assistant",
                user=user,
                access=access,
                session_id="sess-1",
                message="hi",
            )
            try:
                asyncio.run(gen.__anext__())
            except Exception:
                pass
        return resolver, captured

    def test_resolves_by_ref_then_normalizes_to_canonical_id(self):
        resolved = _skill("b153a8d3-uuid", "skill-authoring-assistant", "aitana-platform")
        resolver, captured = self._run(resolved)

        resolver.assert_called_once()
        assert resolver.call_args[0][0] == "skill-authoring-assistant"
        assert captured.get("skill_id") == "b153a8d3-uuid", (
            "downstream must key off the canonical id, not the slug the caller sent"
        )

    def test_missing_and_forbidden_are_distinguishable_server_side(self):
        """Same 404 on the wire, different `reason` for the log."""
        import asyncio

        from auth.firebase_auth import User
        from skills import skill_processor

        user = User(uid="u1", email="u1@example.com", domain="example.com")

        def call(resolved, can_access):
            access = MagicMock()
            access.can_access_skill.return_value = can_access
            with (
                patch.object(skill_processor, "get_skill", return_value=None),
                patch.object(skill_processor, "resolve_skill_ref", return_value=resolved),
            ):
                gen = skill_processor.process_skill_request(
                    skill_id="ref", user=user, access=access, session_id="s", message="hi"
                )
                try:
                    asyncio.run(gen.__anext__())
                except skill_processor.SkillNotFoundError as exc:
                    return exc
            return None

        assert call(None, True).reason == "missing"
        assert call(_skill("id", "slug", "o"), False).reason == "forbidden"
