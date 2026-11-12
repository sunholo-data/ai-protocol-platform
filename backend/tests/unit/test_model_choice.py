"""The composer's Model menu: Auto, Fast, Thorough, and specific models.

Guards the backend half of docs/design/v6.42.0/model-choice.md:

  * what a deployment offers (MODEL_CHOICE) and what it accepts from a client;
  * the specific-model list honours residency and mounted provider keys;
  * a pick reaches every agent in the tree — delegates included — except a
    skill that opts out with `modelChoice: false`;
  * Thorough asks for the highest thinking level;
  * the agent cache never serves an agent built for a different pick;
  * the pick is kept on the chat's row so a reopened chat shows it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from adk import agent_cache, model_choice
from adk.agent import _HeuristicRouter, create_agent, create_agent_with_thinking
from auth.access_context import AccessContext
from auth.firebase_auth import User
from config.models import load_models_config
from config.thinking import ThinkDepth, thinking_config_for
from db.models import DelegationConfig, DelegationMode, SkillConfig, SkillMetadata
from db.models.access import AccessControl


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("MODEL_CHOICE", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    agent_cache.clear()
    yield
    agent_cache.clear()


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx() -> AccessContext:
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com")


def _skill(
    skill_id: str = "s1",
    *,
    model: str = "pro",
    thinking_model: str | None = None,
    allow=None,
    choice: bool = True,
) -> SkillConfig:
    delegation = (
        DelegationConfig(enabled=True, mode=DelegationMode("auto"), allow=allow, max_depth=1)
        if allow
        else DelegationConfig()
    )
    return SkillConfig(
        name=skill_id,
        displayName=skill_id,
        description="A skill.",
        instructions="Do the thing.",
        skillId=skill_id,
        accessControl=AccessControl(type="public"),
        skillMetadata=SkillMetadata(
            model=model, thinkingModel=thinking_model, delegation=delegation, modelChoice=choice
        ),
    )


def _api(agent) -> str:
    """The api name an agent's (possibly chained) model will call first."""
    m = agent.model
    for attr in ("primary", "_primary"):
        m = getattr(m, attr, m)
    chain = getattr(m, "chain", None) or getattr(m, "_chain", None)
    if chain:
        m = chain[0]
    name = getattr(m, "model", m)
    return name.split("/", 1)[-1] if isinstance(name, str) else str(name)


def _with_choice(choice: str):
    return model_choice.ACTIVE_CHOICE.set(choice)


class TestOffered:
    def test_default_offers_everything(self):
        assert model_choice.offered() == "all"

    def test_unknown_value_narrows_to_tiers(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICE", "everything")
        assert model_choice.offered() == "tiers"

    def test_off_turns_every_pick_into_auto(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICE", "off")
        assert model_choice.normalize("thorough") == "auto"


class TestNormalize:
    @pytest.mark.parametrize("raw", [None, "", "auto", 3, "turbo", "model:", "model:not-a-model"])
    def test_anything_unknown_is_auto(self, raw):
        assert model_choice.normalize(raw) == "auto"

    @pytest.mark.parametrize("raw", ["fast", "thorough", " fast "])
    def test_levels_pass(self, raw):
        assert model_choice.normalize(raw) == raw.strip()

    def test_a_listed_model_passes(self):
        assert model_choice.normalize("model:gemini-3-8-flash") == "model:gemini-3-8-flash"

    def test_a_model_the_deployment_cannot_run_is_auto(self):
        # No ANTHROPIC_API_KEY mounted: Claude is not on this deployment's list.
        assert model_choice.normalize("model:claude-opus-5-5") == "auto"

    def test_tiers_only_refuses_specific_models(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICE", "tiers")
        assert model_choice.normalize("model:gemini-3-8-flash") == "auto"
        assert model_choice.normalize("fast") == "fast"


class TestAvailableModels:
    def test_eu_strict_lists_only_eu_models(self, monkeypatch):
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        listed = model_choice.available_models()
        assert listed, "an EU deployment still has models to pick"
        for pick in listed:
            assert (
                load_models_config().models[[m.id for m in load_models_config().models].index(pick.id)].residency
                == "eu"
            )

    def test_labels_are_not_repeated(self):
        labels = [p.label for p in model_choice.available_models()]
        assert len(labels) == len(set(labels))
        # The global entry is listed before its EU sibling, so it wins.
        assert "gemini-3-8-flash" in [p.id for p in model_choice.available_models()]

    def test_provider_keys_gate_the_list(self, monkeypatch):
        assert not any(p.provider == "anthropic" for p in model_choice.available_models())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        assert any(p.provider == "anthropic" for p in model_choice.available_models())

    def test_tiers_only_lists_nothing(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICE", "tiers")
        assert model_choice.available_models() == []


class TestResolveFor:
    def test_auto_leaves_the_skill_alone(self):
        assert model_choice.resolve_for(_skill().skill_metadata) is None

    def test_opted_out_skill_keeps_its_model(self):
        token = _with_choice("fast")
        try:
            assert model_choice.resolve_for(_skill(choice=False).skill_metadata) is None
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)

    def test_levels(self):
        token = _with_choice("thorough")
        try:
            r = model_choice.resolve_for(_skill().skill_metadata)
            assert r == model_choice.Resolved("smart", ThinkDepth.HIGH)
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        token = _with_choice("fast")
        try:
            assert model_choice.resolve_for(_skill().skill_metadata) == model_choice.Resolved("lite", None)
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)


class TestThinkHigh:
    def test_3x_gets_thinking_level_high(self):
        cfg = thinking_config_for(ThinkDepth.HIGH, "gemini-3-8-flash")
        assert cfg.thinking_level is not None and cfg.thinking_level.value == "HIGH"
        assert cfg.include_thoughts is True

    def test_2_5_stays_dynamic(self):
        cfg = thinking_config_for(ThinkDepth.HIGH, "gemini-2.5-flash")
        assert cfg.thinking_budget == -1 and cfg.thinking_level is None

    def test_not_a_skill_setting(self):
        # HIGH is the person's pick, never something a SKILL.md can declare.
        with pytest.raises(ValidationError):
            SkillMetadata(thinking="high")


class TestAgentBuild:
    def test_auto_builds_the_skills_own_model(self):
        agent = create_agent(_skill(model="gemini-3-8-flash"), _user(), access_context=_ctx())
        assert _api(agent) == "gemini-3.8-flash"

    def test_fast_replaces_the_model(self):
        token = _with_choice("fast")
        try:
            agent = create_agent(_skill(model="gemini-3-8-flash"), _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert _api(agent) == "gemini-3.5-flash-lite"

    def test_thorough_asks_for_high_thinking(self, monkeypatch):
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        auto = create_agent(_skill(model="pro"), _user(), access_context=_ctx())
        token = _with_choice("thorough")
        try:
            thorough = create_agent(_skill(model="pro"), _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        # smart == pro on eu-strict today. HIGH measured no deeper than dynamic
        # on 3.x, so the menu hides Thorough there; the request still says HIGH.
        assert auto.planner.thinking_config.thinking_level is None
        assert thorough.planner.thinking_config.thinking_level.value == "HIGH"

    def test_a_picked_non_gemini_model_gets_no_gemini_planner(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        token = _with_choice("model:gpt-6-1-sol")
        try:
            agent = create_agent(_skill(model="pro"), _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert agent.planner is None

    def test_opted_out_skill_keeps_its_model(self):
        token = _with_choice("fast")
        try:
            agent = create_agent(_skill(model="gemini-3-8-flash", choice=False), _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert _api(agent) == "gemini-3.8-flash"

    def test_delegates_follow_the_pick_when_the_door_opts_out(self):
        child = _skill("child", model="gemini-3-8-flash")
        door = _skill("door", model="gemini-3-8-flash", allow=["child"], choice=False)
        token = _with_choice("fast")
        try:
            with patch("adk.agent.get_skill", return_value=child):
                agent = create_agent(door, _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert _api(agent) == "gemini-3.8-flash"
        assert _api(agent.sub_agents[0]) == "gemini-3.5-flash-lite"

    def test_a_pick_replaces_the_thinking_router(self):
        skill = _skill(model="gemini-3-8-flash", thinking_model="gemini-3-8-flash")
        assert isinstance(create_agent_with_thinking(skill, _user(), access_context=_ctx()), _HeuristicRouter)
        token = _with_choice("fast")
        try:
            built = create_agent_with_thinking(skill, _user(), access_context=_ctx())
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert not isinstance(built, _HeuristicRouter)


class TestAgentCache:
    def test_a_different_pick_is_a_different_agent(self):
        skill = _skill()
        calls = []

        def build():
            calls.append(model_choice.ACTIVE_CHOICE.get())
            return object()

        agent_cache.get_or_build(skill, _ctx(), build)
        token = _with_choice("thorough")
        try:
            _, hit = agent_cache.get_or_build(skill, _ctx(), build)
        finally:
            model_choice.ACTIVE_CHOICE.reset(token)
        assert hit is False
        assert calls == ["auto", "thorough"]


class TestChatRow:
    def test_pick_is_stamped_and_auto_clears_it(self):
        from skills.skill_processor import _stamp_model_choice

        with patch("db.chat_sessions.update_session_fields") as upd:
            _stamp_model_choice("t1", None, "thorough")
            upd.assert_called_once_with("t1", {"modelChoice": "thorough"})
            upd.reset_mock()
            _stamp_model_choice("t1", "thorough", "auto")
            upd.assert_called_once_with("t1", {"modelChoice": None})
            upd.reset_mock()
            _stamp_model_choice("t1", "thorough", "thorough")
            _stamp_model_choice("t1", None, "auto")
            _stamp_model_choice("t1", "fast", None)  # caller didn't say
            upd.assert_not_called()

    def test_row_round_trips_the_pick(self):
        from db.chat_sessions import _from_firestore

        row = _from_firestore(
            {
                "skillId": "s1",
                "ownerUid": "u1",
                "accessControl": {"type": "private"},
                "firstMessageAt": "2026-11-06T10:00:00+00:00",
                "lastMessageAt": "2026-11-06T10:00:00+00:00",
                "modelChoice": "fast",
            },
            "t1",
        )
        assert row.model_choice == "fast"


class TestConfig:
    def test_every_level_and_pick_resolves(self):
        cfg = load_models_config()
        ids = {m.id for m in cfg.models}
        assert cfg.choice.fast in cfg.tier_variants or cfg.choice.fast in ids
        assert cfg.choice.thorough in cfg.tier_variants or cfg.choice.thorough in ids
        assert all(p.id in ids for p in cfg.choice.pickable)

    def test_unknown_pickable_fails_loudly(self):
        from config.models import ChoiceConfig, ModelsConfig, PickableModel

        cfg = load_models_config()
        with pytest.raises(ValueError):
            ModelsConfig(
                models=cfg.models,
                defaults=cfg.defaults,
                platform_default=cfg.platform_default,
                tier_variants=cfg.tier_variants,
                choice=ChoiceConfig(pickable=[PickableModel(id="nope", label="Nope")]),
            )


class TestMenuRoute:
    def test_eu_strict_menu_lets_the_ui_see_thorough_equals_pro(self, monkeypatch):
        import asyncio

        from protocols.models_route import model_choice_menu

        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        menu = asyncio.run(model_choice_menu(_user()))
        assert menu.offered == "all"
        assert menu.levels["thorough"].model == menu.resolved["pro"]
        assert menu.levels["fast"].model == menu.resolved["lite"]
        assert menu.labels["pro"] == "Gemini 3.8 Flash"
        assert all(m.provider == "google" for m in menu.models)

    def test_off_offers_no_levels(self, monkeypatch):
        import asyncio

        from protocols.models_route import model_choice_menu

        monkeypatch.setenv("MODEL_CHOICE", "off")
        menu = asyncio.run(model_choice_menu(_user()))
        assert menu.levels == {} and menu.models == []
