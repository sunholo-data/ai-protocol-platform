"""Pydantic round-trip tests for SkillConfig.welcome (v6.4.0 4.5 SKILL-ONBOARDING M1).

Covers null / empty / full / legacy-no-welcome paths so the schema stays
backwards-compatible with older skills written before the welcome field
existed.
"""

from __future__ import annotations

from db.models import (
    BucketBrowserConfig,
    ExampleDocument,
    SkillConfig,
    WelcomeConfig,
)


def test_skill_config_round_trip_with_no_welcome() -> None:
    """Legacy skills written before the welcome field exists round-trip with
    `welcome=None`. Validates additive-nullable backwards-compat."""
    cfg = SkillConfig(name="legacy-skill", description="A legacy skill predating the welcome field")
    assert cfg.welcome is None

    dumped = cfg.model_dump(by_alias=True)
    restored = SkillConfig.model_validate(dumped)
    assert restored.welcome is None


def test_skill_config_round_trip_with_empty_welcome() -> None:
    """`welcome` block can be set with all-empty defaults — represents a skill
    that opts in to the field but hasn't populated any onboarding affordances."""
    cfg = SkillConfig(
        name="empty-welcome-skill",
        description="A skill with an empty welcome block",
        welcome=WelcomeConfig(),
    )
    assert cfg.welcome is not None
    assert cfg.welcome.intro_message is None
    assert cfg.welcome.example_documents == []
    assert cfg.welcome.bucket_browser is None

    dumped = cfg.model_dump(by_alias=True)
    assert dumped["welcome"]["exampleDocuments"] == []
    restored = SkillConfig.model_validate(dumped)
    assert restored.welcome is not None
    assert restored.welcome.example_documents == []


def test_skill_config_round_trip_with_full_welcome() -> None:
    """Full welcome block — intro_message + 5 example_documents + bucket_browser.
    This matches what the one-ppa-expert skill ships with."""
    welcome = WelcomeConfig(
        intro_message="PPA, PtX, BESS — what would you like to analyse?",
        example_documents=[
            ExampleDocument(
                bucket="aitana-examples-public",
                object="ppa/example-A-fixed-pap.pdf",
                label="Example PPA — Fixed price, PaP settlement",
                summary="10-year fixed-price PPA, Pay-as-Produced, German offtaker",
            ),
            ExampleDocument(
                bucket="aitana-examples-public",
                object="ppa/example-B-cpi-pan.pdf",
                label="Example PPA — CPI-indexed, PaN settlement",
            ),
        ],
        bucket_browser=BucketBrowserConfig(
            bucket="multivac-acme-energy-bucket",
            root_path="PPAs/longform/",
            label="Contracts library",
            default_open=False,
        ),
    )
    cfg = SkillConfig(
        name="full-welcome-skill",
        description="A skill with a populated welcome block — Contract Expert shape",
        welcome=welcome,
    )

    # Round-trip through by_alias=True (JSON) then back.
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["welcome"]["introMessage"].startswith("PPA")
    assert len(dumped["welcome"]["exampleDocuments"]) == 2
    assert dumped["welcome"]["bucketBrowser"]["rootPath"] == "PPAs/longform/"

    restored = SkillConfig.model_validate(dumped)
    assert restored.welcome is not None
    assert restored.welcome.intro_message == welcome.intro_message
    assert len(restored.welcome.example_documents) == 2
    assert restored.welcome.example_documents[0].label.startswith("Example PPA")
    assert restored.welcome.bucket_browser is not None
    assert restored.welcome.bucket_browser.bucket == "multivac-acme-energy-bucket"


def test_example_document_optional_fields() -> None:
    """thumbnail and summary are optional. Generic doc-icon fallback per Q1."""
    minimal = ExampleDocument(
        bucket="bucket",
        object="obj.pdf",
        label="A doc",
    )
    assert minimal.thumbnail is None
    assert minimal.summary is None

    full = ExampleDocument(
        bucket="bucket",
        object="obj.pdf",
        label="A doc",
        thumbnail="/img.png",
        summary="One-liner",
    )
    assert full.thumbnail == "/img.png"
    assert full.summary == "One-liner"


def test_bucket_browser_camel_case_aliases() -> None:
    """Aliases round-trip via JSON in camelCase, mirroring the rest of the
    SkillConfig surface so the frontend always sees the same shape."""
    bb = BucketBrowserConfig(
        bucket="bucket",
        root_path="docs/",
        default_open=True,
    )
    dumped = bb.model_dump(by_alias=True)
    assert dumped["rootPath"] == "docs/"
    assert dumped["defaultOpen"] is True

    restored = BucketBrowserConfig.model_validate(dumped)
    assert restored.root_path == "docs/"
    assert restored.default_open is True
