"""Skill materializer — convert between Firestore docs and ADK Skill objects.

Two approaches:
1. skill_from_config() — code-defined, no filesystem (~1ms)
2. materialize_to_dir() — writes SKILL.md directory for load_skill_from_dir() (~10ms)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from google.adk.skills import models

if TYPE_CHECKING:
    from db.models import SkillConfig


def skill_from_config(config: SkillConfig) -> models.Skill:
    """Create an ADK Skill directly from a SkillConfig (no filesystem).

    This is the preferred path for runtime — fast and avoids temp files.
    """
    metadata = config.skill_metadata
    frontmatter_metadata = {
        "author": metadata.author,
        "version": metadata.version,
        "model": metadata.model,
    }
    if metadata.thinking_model:
        frontmatter_metadata["thinkingModel"] = metadata.thinking_model
    if metadata.tools:
        frontmatter_metadata["tools"] = metadata.tools
    if metadata.tool_configs:
        frontmatter_metadata["toolConfigs"] = metadata.tool_configs
    if metadata.sub_skills:
        frontmatter_metadata["subSkills"] = metadata.sub_skills
    if metadata.category:
        frontmatter_metadata["category"] = metadata.category

    return models.Skill(
        frontmatter=models.Frontmatter(
            name=config.name,
            description=config.description,
            metadata=frontmatter_metadata,
        ),
        instructions=config.instructions,
        resources=models.Resources(
            references=config.references,
            assets={k: v.encode() if isinstance(v, str) else v for k, v in config.assets.items()},
        ),
    )


def materialize_to_dir(config: SkillConfig, base_dir: Path | None = None) -> Path:
    """Write a SKILL.md directory that load_skill_from_dir() can load.

    Used for dev/test or when filesystem materialization is needed.
    Returns the skill directory path.
    """
    if base_dir is None:
        base_dir = Path(tempfile.mkdtemp())

    skill_dir = base_dir / config.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Build frontmatter
    metadata = config.skill_metadata
    frontmatter: dict = {
        "name": config.name,
        "description": config.description,
        "metadata": {
            "author": metadata.author,
            "version": metadata.version,
            "model": metadata.model,
        },
    }
    if metadata.thinking_model:
        frontmatter["metadata"]["thinkingModel"] = metadata.thinking_model
    if metadata.tools:
        frontmatter["metadata"]["tools"] = metadata.tools
    if metadata.tool_configs:
        frontmatter["metadata"]["toolConfigs"] = metadata.tool_configs

    # Write SKILL.md
    skill_md = f"---\n{yaml.dump(frontmatter, default_flow_style=False).strip()}\n---\n\n{config.instructions}\n"
    (skill_dir / "SKILL.md").write_text(skill_md)

    # Write references/
    if config.references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for filename, content in config.references.items():
            (refs_dir / filename).write_text(content)

    return skill_dir
