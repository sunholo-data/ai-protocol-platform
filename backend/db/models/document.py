"""Pydantic models for parsed documents.

Mirrors the Firestore schema at docs/design/v6.0.0/document-ui.md lines 291-336.

These models are the canonical Python types for the document-ui feature. They are
consumed by:

- Phase 1B document-ui backend service (`parsed_documents` Firestore collection)
- Phase 1B.2 A2UI render pipeline (reads `a2uiRoot` + `a2uiComponents`)
- Phase 1A.3 ADK FunctionTool `parse_document` (returns `ParsedDocument`)

All field names use camelCase aliases because Firestore stores them in camelCase;
Python code uses snake_case via Pydantic's `populate_by_name=True`.

Block shape is intentionally permissive (`type` + `text` + freeform `properties`)
because the v6.0.0 target runs against `ailang-parse>=0.5.1` whose `Block` is a
dataclass with union fields across block types. The companion JSON schema at
`docs/design/v6.0.0/contracts/document.schema.json` is generated from this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Allowed block discriminator values. Mirrors ailang-parse Block.type
# plus the section/list variants documented in document-ui.md.
BlockType = Literal[
    "heading",
    "paragraph",
    "text",
    "table",
    "image",
    "audio",
    "video",
    "change",
    "list",
    "section",
]


class Block(BaseModel):
    """A single parsed document block.

    Intentionally permissive — the ailang-parse Block is a union-shaped
    dataclass and its concrete fields vary by block type. We pin the
    discriminator (`type`) and the common `text` field, and bag the rest
    into `properties` so Firestore round-trip is lossless.
    """

    type: BlockType
    text: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class DocMetadata(BaseModel):
    """Document metadata extracted by ailang-parse (all optional)."""

    title: str | None = None
    author: str | None = None
    created: str | None = None
    modified: str | None = None
    page_count: int | None = Field(default=None, alias="pageCount")

    model_config = ConfigDict(populate_by_name=True)


class DocSummary(BaseModel):
    """Aggregate counts produced by ailang-parse."""

    total_blocks: int = Field(default=0, alias="totalBlocks")
    headings: int = 0
    tables: int = 0
    images: int = 0
    changes: int = 0

    model_config = ConfigDict(populate_by_name=True)


class EditedBlock(BaseModel):
    """A user edit overlaid onto an original parsed block."""

    original_text: str = Field(alias="originalText")
    edited_text: str = Field(alias="editedText")
    edited_at: datetime = Field(alias="editedAt")
    edited_by: str = Field(alias="editedBy")

    model_config = ConfigDict(populate_by_name=True)


DocumentStatus = Literal["pending", "parsing", "parsed", "failed", "edited"]

ParseStatus = Literal["pending", "parsing", "parsed", "failed"]


class ParsedDocument(BaseModel):
    """Firestore document shape for `parsed_documents/{docId}`.

    Mirrors the schema at docs/design/v6.0.0/document-ui.md:291-336
    extended with file-browser fields (folderId, parseStatus, stats).
    """

    # --- Ownership / source ---
    skill_id: str = Field(alias="skillId")
    user_id: str = Field(alias="userId")
    source_url: str = Field(alias="sourceUrl")
    source_format: str = Field(alias="sourceFormat")
    original_filename: str = Field(alias="originalFilename")
    storage_path: str = Field(alias="storagePath")

    # --- Folder membership (file-browser) ---
    folder_id: str | None = Field(default=None, alias="folderId")
    folder_name: str | None = Field(default=None, alias="folderName")

    # --- File-browser parse lifecycle (separate from document-ui `status`) ---
    parse_status: ParseStatus = Field(default="pending", alias="parseStatus")
    parse_error: str | None = Field(default=None, alias="parseError")
    block_count: int | None = Field(default=None, alias="blockCount")
    table_count: int | None = Field(default=None, alias="tableCount")
    image_count: int | None = Field(default=None, alias="imageCount")
    change_count: int | None = Field(default=None, alias="changeCount")
    parsed_ms: int | None = Field(default=None, alias="parsedMs")

    # --- Lifecycle ---
    status: DocumentStatus = "pending"
    parsed_at: datetime | None = Field(default=None, alias="parsedAt")

    # --- Parsed content ---
    metadata: DocMetadata = Field(default_factory=DocMetadata)
    summary: DocSummary = Field(default_factory=DocSummary)
    blocks: list[Block] = Field(default_factory=list)

    # --- A2UI render artifacts (pre-computed by ailang-parse a2ui formatter) ---
    a2ui_root: str | None = Field(default=None, alias="a2uiRoot")
    a2ui_components: list[dict[str, Any]] | None = Field(default=None, alias="a2uiComponents")

    # --- User edits overlay (sparse map: block-index-string → EditedBlock) ---
    edited_blocks: dict[str, EditedBlock] = Field(default_factory=dict, alias="editedBlocks")

    # --- Timestamps ---
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


__all__ = [
    "Block",
    "BlockType",
    "DocMetadata",
    "DocSummary",
    "DocumentStatus",
    "EditedBlock",
    "ParseStatus",
    "ParsedDocument",
]
