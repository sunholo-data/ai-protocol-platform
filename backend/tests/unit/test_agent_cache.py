"""Built-agent cache: hit/miss + the correctness-critical invalidations (v6.14.0).

The cache must NEVER hand back an agent built for a different skill version or a
different access context — those change the agent (delegate set, per-user
callbacks). It MAY serve a repeat build for the same (skill, updated_at, access).
"""

from __future__ import annotations

import time

import pytest

from adk import agent_cache
from auth.access_context import AccessContext
from db.models import SkillConfig


@pytest.fixture(autouse=True)
def _clear():
    agent_cache.clear()
    yield
    agent_cache.clear()


def _skill(skill_id: str = "s1", updated_at: float = 100.0) -> SkillConfig:
    return SkillConfig(skillId=skill_id, name="s", updatedAt=updated_at)


def _access(uid: str = "u1", tags: set[str] | None = None) -> AccessContext:
    return AccessContext(uid=uid, email=f"{uid}@x.com", domain="x.com", group_tags=frozenset(tags or set()))


def test_hit_for_same_skill_and_access():
    skill, access = _skill(), _access()
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return object()

    a1, hit1 = agent_cache.get_or_build(skill, access, build)
    a2, hit2 = agent_cache.get_or_build(skill, access, build)
    assert hit1 is False and hit2 is True
    assert a1 is a2  # same cached instance
    assert calls["n"] == 1  # built once


def test_miss_on_skill_edit_updated_at_change():
    access = _access()
    a1, _ = agent_cache.get_or_build(_skill(updated_at=100.0), access, object)
    a2, hit = agent_cache.get_or_build(_skill(updated_at=200.0), access, object)  # edited → new updatedAt
    assert hit is False
    assert a1 is not a2


def test_miss_on_different_user():
    skill = _skill()
    a1, _ = agent_cache.get_or_build(skill, _access(uid="u1"), object)
    a2, hit = agent_cache.get_or_build(skill, _access(uid="u2"), object)
    assert hit is False and a1 is not a2


def test_miss_on_group_tag_change():
    skill = _skill()
    a1, _ = agent_cache.get_or_build(skill, _access(tags={"a"}), object)
    a2, hit = agent_cache.get_or_build(skill, _access(tags={"a", "b"}), object)  # e.g. a new grant
    assert hit is False and a1 is not a2


def test_ttl_expiry_rebuilds(monkeypatch):
    skill, access = _skill(), _access()
    monkeypatch.setattr(agent_cache, "_CACHE_TTL", 0.0)  # everything is immediately stale
    a1, _ = agent_cache.get_or_build(skill, access, object)
    a2, hit = agent_cache.get_or_build(skill, access, object)
    assert hit is False and a1 is not a2


def test_lru_eviction_bounds_growth(monkeypatch):
    monkeypatch.setattr(agent_cache, "_CACHE_MAX", 3)
    for i in range(5):
        agent_cache.get_or_build(_skill(skill_id=f"s{i}"), _access(), object)
    assert len(agent_cache._cache) == 3  # capped


def test_build_error_is_not_cached():
    skill, access = _skill(), _access()

    def boom():
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError):
        agent_cache.get_or_build(skill, access, boom)
    # A subsequent successful build must run (the error was not cached).
    ok, hit = agent_cache.get_or_build(skill, access, lambda: "ok")
    assert hit is False and ok == "ok"


def test_repeated_hits_do_not_rebuild_over_time(monkeypatch):
    skill, access = _skill(), _access()
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return object()

    agent_cache.get_or_build(skill, access, build)
    now[0] += 30  # within the 60s TTL
    _, hit = agent_cache.get_or_build(skill, access, build)
    assert hit is True and calls["n"] == 1
