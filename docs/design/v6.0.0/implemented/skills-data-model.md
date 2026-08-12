# Skills Data Model

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 4 days
**Actual**: 1 day (2026-04-13)
**Scope**: Fullstack (backend + frontend types + JSON Schema contract)
**Dependencies**: None (foundational)
**Created**: 2026-04-10
**Last Updated**: 2026-04-21
**Sprint**: SKILLS-DATA-MODEL (3 milestones, 1533 LOC, 70 tests passing)

## Problem Statement

Skills are v6's core abstraction. Two established standards define skills:

1. **Agent Skills spec** ([agentskills.io/specification](https://agentskills.io/specification)) — the open standard for skill definition, used by Claude Code, ADK, and other agent frameworks. Skills are defined as `SKILL.md` files with YAML frontmatter (`name`, `description`) and markdown instructions, plus optional `references/`, `assets/`, and `scripts/` directories.

2. **Google ADK Skills** ([adk.dev/skills](https://adk.dev/skills/)) — ADK v1.25.0+ (experimental) adds `load_skill_from_dir()` and `SkillToolset`. ADK's `models.Skill` class maps directly to the Agent Skills spec. **v6 requires ADK >= 1.25.0.**

The v6 design aligns with these standards. Platform-specific fields (access control, protocols, marketplace metadata) wrap the spec-aligned core as a separate metadata layer.

**Current State (post-implementation):**
- `backend/db/models.py` — SkillConfig with field validators aligned to Agent Skills spec
- `backend/skills/skill_materializer.py` — Firestore → ADK Skill (code-defined + filesystem paths)
- `backend/skills/skill_config.py` — CRUD service with 60s TTL in-memory cache
- `backend/skills/routes.py` — FastAPI router at `/api/skills` (6 endpoints)
- `backend/skills/templates/` — 5 SKILL.md seed templates
- `frontend/src/types/skill.ts` — TypeScript interfaces mirroring backend models
- `docs/design/v6.0.0/contracts/skill.schema.json` — JSON Schema contract
- 70 tests across 4 test files (models, materializer, CRUD service, API)

**Impact:**
- Blocks all backend work (agent factory, skill processor, channels, protocols)
- Blocks frontend work (marketplace, skill builder, chat)
- Wrong foundation = wrong everything downstream

## Goals

**Primary Goal:** Define a skill storage model that aligns with the Agent Skills spec and ADK's `load_skill_from_dir()` / `SkillToolset`, augmented with Aitana-specific metadata (access control, protocols, marketplace) as a separate layer.

**Success Metrics:**
- Skills stored in Firestore can be materialized as valid SKILL.md directories for ADK
- ADK's `load_skill_from_dir()` can load materialized skill directories
- `SkillToolset` can expose skills as tools to the root agent
- CRUD API supports create, read, update, delete, list with filtering
- Seed templates cover the 5 most-used v5 assistants

**Non-Goals:**
- Marketplace ranking/recommendation algorithm (Phase 4)
- Skill versioning or rollback (future)
- Skill sharing between organizations (single-org for now)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Data model, not latency path |
| 2 | EARNED TRUST | 0 | No user-facing claims |
| 3 | SKILLS, NOT FEATURES | +1 | Skills are the core abstraction; aligned with industry standard |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Model config per skill enables routing |
| 5 | GRACEFUL DEGRADATION | 0 | N/A for data model |
| 6 | PROTOCOL OVER CUSTOM | +1 | **Aligns with Agent Skills spec + ADK Skills API** |
| 7 | API FIRST | +1 | CRUD API serves all channels |
| 8 | OBSERVABLE BY DEFAULT | 0 | N/A for data model |
| 9 | SECURE BY CONSTRUCTION | +1 | Access control enforced at data layer |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | N/A for data model |
| | **Net Score** | **+5** | Threshold: >= +4 |

## Design

### Overview

v6 skills use the **same Agent Skills spec format** as Claude Code, Gemini CLI, and Google's Agents CLI. The difference is distribution: v6 stores skills in Firestore with multi-tenant access control and exposes user-facing CRUD, while those tools ship skills statically to developer machines. Same SKILL.md format, same `load_skill_from_dir()` loader, same `SkillToolset` — different audience (end users vs. developers) and different storage (Firestore vs. filesystem).

Skills have two layers:

1. **Skill Definition** — follows the Agent Skills spec (SKILL.md format). This is what ADK loads and what the agent sees. Stored as structured fields in Firestore.
2. **Aitana Metadata** — platform-specific fields (access control, protocols, marketplace tags, ownership). This wraps the skill definition with operational data that ADK doesn't need.

The agent factory materializes layer 1 into a directory that `load_skill_from_dir()` can load. Layer 2 is used by the API, marketplace, and permission system.

### The Agent Skills Spec (What We Adopt)

Per [agentskills.io/specification](https://agentskills.io/specification):

```
skill-name/
├── SKILL.md          # Required: YAML frontmatter + markdown instructions
├── references/       # Optional: additional docs loaded on demand
├── assets/           # Optional: templates, schemas, data files
└── scripts/          # Optional: executable code
```

**SKILL.md format:**
```markdown
---
name: document-analyst
description: >
  Analyze documents, extract structured data, and answer questions about
  uploaded files. Use when the user uploads a document, asks about file
  contents, or requests data extraction.
license: Proprietary
metadata:
  author: aitana
  version: "1.0"
---

You are a document analysis expert. When the user provides a document:

1. Use the file_browser tool to locate and access the file
2. Use ai_search to find relevant content within document collections
3. Use structured_extraction to pull structured data from documents

Always cite the source document and page/section for factual claims.
Provide confidence scores for extracted data.
```

**Key spec constraints:**
- `name`: 1-64 chars, lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens
- `description`: 1-1024 chars, describes what + when to use
- `metadata`: arbitrary key-value pairs (we use this for Aitana-specific config)
- Instructions (body): the agent's system prompt, loaded when skill activates
- Progressive disclosure: metadata (~100 tokens) loaded at startup, full SKILL.md on activation, references/ on demand

### How ADK Loads Skills

```python
# ADK's native skill loading (v1.25.0+, experimental)
from google.adk.skills import load_skill_from_dir, models
from google.adk.tools.skill_toolset import SkillToolset

# Load from directory
weather_skill = load_skill_from_dir(
    pathlib.Path(__file__).parent / "skills" / "weather_skill"
)

# Or define in code (same schema)
greeting_skill = models.Skill(
    frontmatter=models.Frontmatter(
        name="greeting-skill",
        description="A friendly greeting skill...",
    ),
    instructions="Step 1: Read the references...",
    resources=models.Resources(
        references={"guide.md": "Reference content here..."},
    ),
)

# Expose skills as tools to the root agent
my_skills = SkillToolset(skills=[weather_skill, greeting_skill])

root_agent = Agent(
    model="gemini-2.5-flash",
    name="aitana",
    tools=[my_skills],
)
```

### Firestore Schema

The Firestore document stores both the Agent Skills spec fields AND Aitana metadata:

```
skills/{skillId}

  # === Agent Skills Spec (Layer 1) ===
  # These fields map directly to SKILL.md frontmatter + body
  
  name: string                       # SKILL.md frontmatter: name (1-64 chars, lowercase-hyphen)
  description: string                # SKILL.md frontmatter: description (1-1024 chars)
  instructions: string               # SKILL.md body: the agent's system prompt
  
  # SKILL.md frontmatter: metadata (arbitrary key-value)
  skillMetadata: {
    author: string                   # e.g., "aitana"
    version: string                  # e.g., "1.0"
    model: string                    # e.g., "gemini-2.5-flash"
    thinkingModel: string?           # Optional reasoning model
    tools: string[]                  # Tool names the agent can use
    toolConfigs: map                 # Per-tool config (e.g., {"ai_search": {"datastore": "ds-123"}})
    subSkills: string[]              # Skill IDs this skill can delegate to (max 5)
  }

  # SKILL.md references/ directory content
  references: map<string, string>    # filename → content (e.g., {"guide.md": "..."})
  
  # SKILL.md assets/ directory content  
  assets: map<string, string>        # filename → GCS URL (binary assets stored in GCS)
  
  # === Aitana Platform Metadata (Layer 2) ===
  # Platform-specific fields that ADK doesn't need
  
  skillId: string                    # UUID v4 (Firestore document ID)
  displayName: string                # Human-friendly name (e.g., "Document Analyst") — for UI
  avatar: string                     # URL or emoji — for UI
  
  ownerEmail: string                 # Creator's email
  ownerId: string                    # Firebase Auth UID
  
  accessControl: {
    type: "private" | "public" | "domain" | "specific"
    domain: string?                  # e.g., "aitana.ai" — if type="domain"
    emails: string[]?                # Allowed emails — if type="specific"
  }
  
  protocols: {
    mcp: { enabled: bool }           # Expose as MCP tool
    a2a: { enabled: bool }           # Expose in A2A agent card
    agui: { enabled: bool }          # AG-UI streaming (default: true)
    a2ui: { enabled: bool }          # Declarative UI responses
    mcpApps: { enabled: bool }       # Interactive tool UIs
  }
  
  initialMessage: string             # Greeting message shown on first load
  tags: string[]                     # Freeform tags for marketplace discovery
  featured: bool                     # Admin-curated for marketplace home
  usageCount: number                 # Incremented on each conversation start
  
  createdAt: timestamp
  updatedAt: timestamp
  v5AssistantId: string?             # Migration backlink
```

### Materialization: Firestore → SKILL.md Directory

The agent factory materializes a Firestore skill document into the directory structure that `load_skill_from_dir()` expects:

```python
# backend/skills/skill_materializer.py

import tempfile
from pathlib import Path
from google.adk.skills import load_skill_from_dir

def materialize_skill(skill_doc: dict) -> Path:
    """Create a SKILL.md directory from a Firestore skill document."""
    skill_dir = Path(tempfile.mkdtemp()) / skill_doc["name"]
    skill_dir.mkdir(parents=True)
    
    # Write SKILL.md
    frontmatter = {
        "name": skill_doc["name"],
        "description": skill_doc["description"],
        "metadata": skill_doc.get("skillMetadata", {}),
    }
    
    skill_md = f"""---
{yaml.dump(frontmatter, default_flow_style=False).strip()}
---

{skill_doc["instructions"]}
"""
    (skill_dir / "SKILL.md").write_text(skill_md)
    
    # Write references/
    references = skill_doc.get("references", {})
    if references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        for filename, content in references.items():
            (refs_dir / filename).write_text(content)
    
    return skill_dir


def load_skill_from_firestore(skill_doc: dict):
    """Load an ADK Skill from a Firestore document."""
    skill_dir = materialize_skill(skill_doc)
    return load_skill_from_dir(skill_dir)
```

**Alternative: Code-defined skills (no filesystem):**

```python
from google.adk.skills import models

def skill_from_firestore(skill_doc: dict) -> models.Skill:
    """Create an ADK Skill directly from Firestore data (no filesystem)."""
    return models.Skill(
        frontmatter=models.Frontmatter(
            name=skill_doc["name"],
            description=skill_doc["description"],
        ),
        instructions=skill_doc["instructions"],
        resources=models.Resources(
            references=skill_doc.get("references", {}),
        ),
    )
```

### Agent Factory Integration

The root agent uses `SkillToolset` to expose skills:

```python
# backend/adk/agent.py

from google.adk import Agent
from google.adk.tools import skill_toolset
from backend.skills.skill_materializer import skill_from_firestore

async def create_root_agent(user_skills: list[dict]) -> Agent:
    """Create the root agent with user's skills as tools."""
    skills = [skill_from_firestore(doc) for doc in user_skills]
    
    return Agent(
        model="gemini-2.5-flash",
        name="aitana",
        description="Aitana AI assistant with specialized skills.",
        instruction="You are Aitana, an AI assistant. Use your available skills to help the user.",
        tools=[
            skill_toolset.SkillToolset(skills=skills),
        ],
    )
```

### Resolved Design Decisions

#### 1. Multi-Model Support

**Decision: Yes — stored in `skillMetadata.model` and `skillMetadata.thinkingModel`.**

The Agent Skills spec's `metadata` field is explicitly designed for arbitrary key-value pairs. We store model selection there rather than inventing custom top-level fields:

```yaml
metadata:
  model: gemini-2.5-flash
  thinkingModel: gemini-2.5-pro
```

The agent factory reads these from metadata when creating the ADK agent.

#### 2. Tool Configuration

**Decision: Tools stored in `skillMetadata.tools` and `skillMetadata.toolConfigs`.**

Tool names and per-tool configuration are platform-specific (which Vertex Search datastore, which GCS bucket). The Agent Skills spec's metadata field handles this:

```yaml
metadata:
  tools:
    - ai_search
    - file_browser
    - structured_extraction
  toolConfigs:
    ai_search:
      datastore: ds-documents
```

The skill's `instructions` (SKILL.md body) tells the agent *how* to use tools; the metadata tells the platform *which* tools to wire up.

#### 3. Sub-Skill Delegation

**Decision: Inline context — sub-skill results merge into the parent conversation.**

Sub-skills are referenced by name in `skillMetadata.subSkills`. The agent factory loads them as ADK `sub_agents`. Max 5, no circular references.

#### 4. Marketplace Categories

**Decision: Tag-based discovery with predefined category tags.**

```python
SKILL_CATEGORIES = [
    "document-analysis", "search", "code", "data",
    "communication", "extraction", "creative", "admin",
]
```

Tags are Aitana metadata (layer 2), not part of the Agent Skills spec.

### Implemented Pydantic Models

> **Source of truth:** `backend/db/models.py`. The code below is a snapshot; refer to the actual file for current state.

Key implementation decisions vs. the original design:

1. **Pydantic v2 with aliases** — All models use `Field(alias="camelCase")` + `populate_by_name=True` so they accept both Python snake_case and Firestore camelCase. `model_dump(by_alias=True)` produces Firestore-ready documents.

2. **Field validators** — The Agent Skills spec constraints are enforced at the Pydantic layer, not at the API layer. This means invalid skills are rejected regardless of entry point (API, seed script, direct Firestore write).

3. **Description must be non-empty** — The original design showed `description: str = ""` but ADK's `Frontmatter` rejects empty descriptions. The validator now enforces 1-1024 chars, matching both the Agent Skills spec and ADK's actual behavior. This was caught during sprint evaluation.

4. **Auto-generated fields** — `skillId` defaults to `uuid4()`, `createdAt`/`updatedAt` default to `time.time()`. No need to pass these on creation.

```python
# backend/db/models.py (implemented)
# See actual file for full code — this shows the key structural decisions

class SkillMetadata(BaseModel):
    author: str = "aitana"
    version: str = "1.0"
    model: str = "gemini-2.5-flash"
    thinking_model: str | None = Field(default=None, alias="thinkingModel")
    tools: list[str] = []
    tool_configs: dict = Field(default_factory=dict, alias="toolConfigs")
    sub_skills: list[str] = Field(default_factory=list, alias="subSkills")
    model_config = {"populate_by_name": True}

class SkillConfig(BaseModel):
    # Layer 1: Agent Skills spec
    name: str                    # Validated: 1-64 chars, lowercase kebab-case
    description: str = ""        # Validated: 1-1024 chars, non-empty
    instructions: str = ""       # Validated: max 10,000 chars
    skill_metadata: SkillMetadata = Field(default_factory=SkillMetadata, alias="skillMetadata")
    references: dict[str, str] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict)

    # Layer 2: Aitana platform metadata
    skill_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="skillId")
    display_name: str = Field(default="", alias="displayName")
    # ... (full model in backend/db/models.py)
    model_config = {"populate_by_name": True}
```

### Validation Rules

**Agent Skills spec constraints (enforced):**
- `name`: 1-64 chars, lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens
- `description`: 1-1024 chars, non-empty
- `instructions`: max 10,000 chars

**Aitana constraints:**
- `displayName`: required, 1-100 chars
- `skillMetadata.tools`: each must be a registered tool name
- `skillMetadata.subSkills`: max 5, each must be an existing skill name, no circular references
- `skillMetadata.model`: must be a supported model ID
- `accessControl.type`: must be one of `private`, `public`, `domain`, `specific`
- `tags`: max 10 tags, each max 50 chars

### Firestore Indexes

```
# Marketplace: list public skills by category
skills — tags (ARRAY_CONTAINS) + usageCount (DESC)

# Marketplace: list public skills by recency
skills — accessControl.type (==) + updatedAt (DESC)

# User's skills: list by owner
skills — ownerId (==) + updatedAt (DESC)

# Domain skills: list skills shared with a domain
skills — accessControl.type (==) + accessControl.domain (==) + updatedAt (DESC)

# Featured skills
skills — featured (==) + usageCount (DESC)

# Migration lookup
skills — v5AssistantId (==)
```

### CRUD API Contract

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/api/skills` | Create a new skill | Required |
| `GET` | `/api/skills` | List skills (filtered) | Required |
| `GET` | `/api/skills/{skillId}` | Get skill by ID | Required (+ access check) |
| `PUT` | `/api/skills/{skillId}` | Update skill | Required (owner only) |
| `DELETE` | `/api/skills/{skillId}` | Delete skill | Required (owner only) |
| `GET` | `/api/skills/marketplace` | Public skills for marketplace | Optional |

#### Create Skill Example

```json
// POST /api/skills
{
  "name": "document-analyst",
  "displayName": "Document Analyst",
  "description": "Analyze documents, extract structured data, and answer questions. Use when user uploads documents or asks about file contents.",
  "instructions": "You are a document analysis expert...\n\n1. Use file_browser to locate files\n2. Use ai_search for document collections\n3. Use structured_extraction for data extraction\n\nAlways cite sources and provide confidence scores.",
  "skillMetadata": {
    "model": "gemini-2.5-flash",
    "thinkingModel": "gemini-2.5-pro",
    "tools": ["ai_search", "file_browser", "structured_extraction"],
    "toolConfigs": {
      "ai_search": { "datastore": "ds-documents" }
    }
  },
  "avatar": "📄",
  "accessControl": { "type": "domain", "domain": "aitana.ai" },
  "tags": ["document-analysis", "extraction"],
  "initialMessage": "Hello! Upload a document or ask me to search."
}
```

### Seed Templates

Five seed templates as SKILL.md-compatible definitions:

| Template | name | model | tools | tags |
|----------|------|-------|-------|------|
| Document Analyst | `document-analyst` | gemini-2.5-flash + thinking: gemini-2.5-pro | ai_search, file_browser, structured_extraction | document-analysis, extraction |
| Web Researcher | `web-researcher` | gemini-2.5-flash | google_search, url_processing | search |
| Code Assistant | `code-assistant` | gemini-2.5-flash + thinking: gemini-2.5-pro | code_execution | code |
| Data Extractor | `data-extractor` | gemini-2.5-flash | structured_extraction, file_browser | extraction, data |
| General Assistant | `general-assistant` | gemini-2.5-flash | google_search, file_browser | general |

Seed templates can also be shipped as actual SKILL.md directories in `backend/skills/templates/` for development/testing.

### Architecture Diagram

```
[Firestore: skills/{skillId}]
         │
         ▼
[skill_materializer.py]
  ├── Read Firestore doc
  ├── Build models.Skill (or materialize SKILL.md dir)
  └── Return ADK Skill object
         │
         ▼
[SkillToolset(skills=[...])]
         │
         ▼
[Root Agent — tools=[skill_toolset]]
         │
         ▼
[ADK agent loop — selects + activates skills as needed]
```

## Implementation Plan (Completed 2026-04-13)

### M1: Models + Materializer (547 LOC)
- [x] Rewrote `backend/db/models.py` with spec-aligned schema + field validators
- [x] Implemented `backend/skills/skill_materializer.py` — both `skill_from_config()` (code-defined, ~1ms) and `materialize_to_dir()` (filesystem, ~10ms)
- [x] Validated `name` field against Agent Skills spec constraints (kebab-case, 1-64 chars)
- [x] Added description validation (non-empty, 1-1024 chars) — caught during evaluation that ADK Frontmatter rejects empty descriptions
- [x] 44 tests in `test_models.py` + 9 tests in `test_materializer.py`

### M2: CRUD Service (469 LOC)
- [x] Rewrote `backend/skills/skill_config.py` (create, get, list, update, delete, marketplace, increment_usage)
- [x] Added filtering by owner_id, tag (ARRAY_CONTAINS), access_type
- [x] Added in-memory cache with 60s TTL + expiry test
- [x] Rewrote `backend/db/firestore.py` as standalone sync client (not async — matches Cloud Run concurrency model)
- [x] 15 tests in `test_skill_config.py`

**Deferred:** Circular sub-skill validation, tool name registry check, model ID validation — these depend on agent-factory.md and tool registry being implemented.

### M3: API Routes + Seed Data (517 LOC)
- [x] Created `backend/skills/routes.py` as separate router module (included via `app.include_router`)
- [x] 6 endpoints: POST (201), GET list, GET marketplace, GET by ID, PUT (partial update), DELETE (204)
- [x] 5 SKILL.md seed templates in `backend/skills/templates/`
- [x] `backend/scripts/seed_skills.py` — idempotent Firestore seeder with --dry-run
- [x] `frontend/src/types/skill.ts` — TypeScript interfaces mirroring backend models
- [x] `docs/design/v6.0.0/contracts/skill.schema.json` — generated from `SkillConfig.model_json_schema(by_alias=True)`
- [x] 12 tests in `test_skills_api.py`

**Deferred:** Firestore composite indexes (need `firestore.indexes.json` — deploy-time concern), auth middleware on PUT/DELETE (see auth-and-permissions.md)

## Migration & Rollout

**Database Migrations:**
- New `skills/` collection (v5 `assistants/` untouched)
- Composite indexes created via `firestore.indexes.json`

**Rollback Plan:**
- Delete `skills/` collection — v5 `assistants/` is unaffected

## Testing Strategy (70 tests implemented)

### Backend Tests (pytest) — 70 passing

**`test_models.py` (44 tests):**
- [x] Pydantic model validation — defaults, full config, round-trip
- [x] Name validation — 6 valid cases, 10 invalid cases (parametrized)
- [x] Description validation — empty rejected, max length, too long
- [x] Instructions validation — max length, too long
- [x] Tags validation — max count, individual tag length
- [x] AccessControl type validation — 4 valid types, invalid rejected
- [x] SkillMetadata alias round-trip (camelCase ↔ snake_case)

**`test_materializer.py` (9 tests):**
- [x] `skill_from_config()` — basic, with metadata, with references, no optional fields, empty refs
- [x] `materialize_to_dir()` — SKILL.md written, references dir, no refs dir when empty
- [x] Round-trip: `materialize_to_dir()` → `load_skill_from_dir()` succeeds

**`test_skill_config.py` (15 tests):**
- [x] CRUD — create, get, update, delete (success + not-found cases)
- [x] Cache — hit test, TTL expiry test (backdated timestamp)
- [x] Filters — by owner_id, tag (ARRAY_CONTAINS), access_type
- [x] Marketplace — sorted by usageCount
- [x] `increment_usage()` — atomic field increment

**`test_skills_api.py` (12 tests):**
- [x] All 6 endpoints tested via FastAPI TestClient
- [x] Error cases — 404 on missing skill, 400 on empty update

**Deferred:**
- [ ] Round-trip against external SKILL.md (Agents CLI bundled skills) — needs skill import endpoint
- [ ] CRUD against Firestore emulator — currently uses mocked Firestore
- [ ] Circular sub-skill rejection — needs agent-factory.md
- [ ] Seed script idempotency test — manual verification only (requires live Firestore)

## Security Considerations

- Owner-only write access (create/update/delete)
- Read access governed by `accessControl` field
- No PII in skill configs (owner email is the only user data)
- `instructions` is user-authored — sanitize for Firestore injection
- Skill `name` validated against spec (no injection via name field)

## Performance Considerations

- Skill configs cached in-memory with 60s TTL
- Marketplace queries use composite indexes (no full-collection scans)
- `usageCount` incremented via Firestore `increment()` (atomic)
- Materialization is lightweight (~1ms for code-defined skills, ~10ms for filesystem)

## Success Criteria

- [x] Skills conform to Agent Skills spec (`name` format, `description` constraints) — enforced by Pydantic validators, 44 model tests
- [x] Materialized skills load via ADK `load_skill_from_dir()` — verified in `test_materializer.py::test_materialize_round_trip`
- [ ] `SkillToolset` exposes skills to root agent — blocked on agent-factory.md implementation
- [x] CRUD operations work against mocked Firestore — 15 service tests + 12 API tests
- [x] Marketplace query returns skills filtered by tag, sorted by usage — `list_marketplace()` with `order_by=usageCount`
- [x] Seed templates load as valid ADK skills — 5 templates, each parseable by materializer
- [x] Lint and typecheck clean — `make lint` + `npm run quality:check:fast` both pass

## Resolved Questions

- **Importing external SKILL.md files** — **Yes.** v6 will accept SKILL.md directories (tarball or zip) via `POST /api/skills/import`. The Agent Skills spec is a real interop format used by Claude Code, Gemini CLI, and Google's Agents CLI; users should be able to import skills authored externally and export their Aitana skills back to that format. This makes v6's marketplace bidirectionally compatible with the broader skill ecosystem.

## Implementation Notes (2026-04-13)

### Deviations from Original Design

1. **Sync Firestore client, not async.** The original design implied async I/O. Implementation uses synchronous `google-cloud-firestore` calls. Rationale: Cloud Run uses a process-per-request concurrency model; async gains nothing and adds complexity. If we move to async FastAPI later, the Firestore client is isolated in `db/firestore.py` and easy to swap.

2. **Routes in separate module.** The design said "add FastAPI routes to `fast_api_app.py`". Implementation creates `backend/skills/routes.py` as a separate `APIRouter` included via `app.include_router(skills_router)`. This keeps `fast_api_app.py` as a thin app factory and avoids a 300+ line monolith.

3. **Frontend types + JSON Schema contract added.** Not in the original design but essential for fullstack development. `frontend/src/types/skill.ts` mirrors the backend Pydantic models. `skill.schema.json` was generated from `SkillConfig.model_json_schema(by_alias=True)` for contract validation.

4. **Description validation tightened.** Original design showed `description: str = ""`. ADK's `Frontmatter` class rejects empty descriptions at runtime. The validator now enforces non-empty (1-1024 chars), matching both the Agent Skills spec text and ADK's actual behavior.

5. **Auth middleware deferred.** PUT and DELETE routes have TODO comments for ownership checks. The design doc references `auth-and-permissions.md` which hasn't been implemented yet. Routes currently allow unauthenticated access — this is intentional for development velocity and must be locked down before any deployment.

### Stack Upgrade (same session)

During implementation, the frontend dependency stack was upgraded to unblock `npm install`:

- **React 18 → React 19** (`^19.0.0`) — greenfield project, no migration burden
- **Next.js 14 → Next.js 15** (`^15.5.0`) — required for React 19 peer dep
- **Removed `@a2ui/web-lib`** — package doesn't exist on npm (never published)
- **Fixed `@ag-ui/core`** version: `^0.1.0` → `^0.0.52` (0.1.0 doesn't exist)
- **Fixed `@mcp-ui/client`** version: `^0.1.0` → `^7.0.0` (no 0.x versions exist)
- **`@a2ui/react`** pinned to `^0.9.0-alpha.0` (Google official, supports React 18+19)
- **Added `@vitejs/plugin-react`** and **`jsdom`** as missing dev dependencies

All protocol packages (`@a2ui/react`, `@ag-ui/core`, `@copilotkit/react-core`, `@copilotkit/react-ui`, `@mcp-ui/client`) install cleanly. None are imported yet — they're ready for the AG-UI streaming and A2UI rendering sprints.

## Open Questions

- Should `usageCount` be a separate analytics collection to avoid write contention on hot skills?
- Should skill instructions support template variables (e.g., `{{user.name}}`)?
- ADK's `SkillToolset` is experimental (v1.25.0+) — monitor for breaking changes

## Related Documents

- [Agent Skills Specification](https://agentskills.io/specification) — the open standard we adopt
- [ADK Skills](https://adk.dev/skills/) — ADK's skill loading and toolset API
- [Product Axioms](../../product-axioms.md) — Axiom #6: Protocol Over Custom
- [Migration to v6](../v5.0.0/migration-to-v6.md) — Skills collection schema (lines 399-434)
- [Auth & Permissions](../auth-and-permissions.md) — Access control enforcement
- [Agent Factory](agent-factory.md) — How SkillConfig becomes a running agent

---

## Implementation Report

**Completed**: 2026-04-21
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
