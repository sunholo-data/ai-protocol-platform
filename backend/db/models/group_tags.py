"""Group-tag registry model (v6.9.0 / 9.3 user-group-administration).

Makes an identity/group tag *first-class* — more than a free string riding in
the signed JWT ``groupTags`` claim. Each registry entry records what a tag is
called, what it grants, and (optionally) which tenant it is scoped to, so:

  - grant/revoke can validate a tag id against a known vocabulary (reject
    typos with a 422 instead of silently minting a useless claim), and
  - an admin surface can show "what does this tag unlock" and "who holds it".

Stored one-doc-per-tag in the Firestore ``group_tags`` collection (doc id == the
tag id). Distinct from ``firestore.rules`` ``/tags/{tagId}`` (a resource-tag
vocabulary) — do NOT conflate the two.

See docs/design/v6.9.0/user-group-administration.md.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field


class GroupTag(BaseModel):
    """One entry in the identity/group-tag registry (``group_tags/{id}``).

    ``id`` is the canonical tag string as it appears in the JWT ``groupTags``
    claim (e.g. ``"ONE"``, ``"aitana-admin"``). ``grants`` documents *what the
    tag unlocks* (skill slugs / tool names that reference it) — advisory
    metadata for the admin UI, not an enforcement input. ``tenant_scope`` marks
    a tag as belonging to a single tenant domain (``None`` == platform-wide);
    it bounds who may manage the entry once tenant-admin management lands.
    """

    id: str = Field(min_length=1)
    label: str = ""
    description: str = ""
    grants: list[str] = Field(default_factory=list)
    tenant_scope: str | None = Field(default=None, alias="tenantScope")
    created_by: str = Field(default="", alias="createdBy")
    created_at: float = Field(default_factory=time.time, alias="createdAt")

    model_config = {"populate_by_name": True}


__all__ = ["GroupTag"]
