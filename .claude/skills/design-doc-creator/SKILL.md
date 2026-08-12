---
name: design-doc-creator
description: Create design documents for Aitana Labs features in the correct format and location. Use when user asks to create a design doc, plan a feature, write a spec, or document a design. Also use when user says "design", "RFC", "proposal", or wants to think through a feature before implementing. Handles both planned and implemented docs.
---

# Design Doc Creator

Create well-structured design documents for Aitana Labs features following the project's conventions.

## Quick Start

```bash
# User says: "Create a design doc for adding Slack integration"
# This skill will:
# 1. Search for existing related design docs
# 2. Create docs/design/<version>/slack-integration.md
# 3. Fill template with proper structure (problem, goals, design, testing)
# 4. Include frontend/backend impact analysis
```

## When to Use This Skill

Invoke this skill when:
- User asks to "create a design doc", "write a design doc", or "write a spec"
- User says "plan a feature" or "design a feature"
- User mentions "RFC", "proposal", or "technical design"
- Before starting implementation of a non-trivial feature
- After completing a feature (to move doc to implemented/)

## Available Scripts

### `scripts/create_design_doc.sh <version> <doc-name>`
Create a new design document in `docs/design/v<version>/`.

```bash
.claude/skills/design-doc-creator/scripts/create_design_doc.sh 6.0.0 slack-integration
# Creates: docs/design/v6.0.0/slack-integration.md
```

### `scripts/move_to_implemented.sh <doc-name>`
Move a completed design document into its version's `implemented/` subfolder
and flip the Status frontmatter. Idempotent.

```bash
.claude/skills/design-doc-creator/scripts/move_to_implemented.sh slack-integration
# docs/design/v6.0.0/slack-integration.md
#   -> docs/design/v6.0.0/implemented/slack-integration.md
# Also moves the companion *-sprint.md if present.
# Flips **Status**: Planned|Proposed -> Implemented, refreshes Last Updated,
# and appends an Implementation Report stub to the design doc.
```

## Workflow

### Creating a Design Doc

**1. Gather Requirements**

Ask the user:
- What feature are you designing?
- What priority? (P0/P1/P2)
- Estimated effort? (e.g., 2 days, 1 week)
- Frontend-only, backend-only, or fullstack?
- Any dependencies on other features?

**2. Check for Existing Work**

Before writing, search for related design docs and existing code:

```bash
# Check existing design docs
ls docs/design/ docs/design/implemented/

# Search for related components/services
grep -r "FeatureName" src/ backend/ --include="*.tsx" --include="*.ts" --include="*.py"
```

**3. Audit for Systemic Issues**

Before writing a design doc for a bug fix, ALWAYS ask: "Is this part of a larger pattern?"

**The Anti-Pattern (incremental special-casing):**
```
v1: Add feature for case A
v2: Bug! Add special case for B
v3: Bug! Add special case for C
...forever patching
```

**Analysis Checklist:**
- [ ] Is this a one-off or part of a pattern?
- [ ] Search codebase for similar code paths
- [ ] Check if other types/cases have the same gap
- [ ] Look at git history - has this area been patched repeatedly?
- [ ] Design fix to cover ALL cases, not just the reported one

**4. Run Create Script**

```bash
.claude/skills/design-doc-creator/scripts/create_design_doc.sh <version> feature-name
# e.g.: .claude/skills/design-doc-creator/scripts/create_design_doc.sh 6.0.0 feature-name
```

**5. Customize the Template**

Fill in all sections of the generated template. Key sections:

- **Header**: Feature name, status, priority, estimated effort
- **Problem Statement**: Current pain points with metrics
- **Goals**: Primary goal + measurable success metrics
- **Axiom Alignment**: Score against product axioms (see step 5a)
- **Design**: Architecture overview with frontend AND backend sections
- **API Changes**: New/modified endpoints
- **Migration**: Database changes, feature flags, rollback plan
- **Testing Strategy**: Frontend tests (Vitest), backend tests (pytest), E2E
- **Success Criteria**: Checkboxes for acceptance tests

**5a. Score Against Product Axioms (MANDATORY)**

Read `docs/product-axioms.md` and fill the **Axiom Alignment** table in the design doc:

1. Score each of the 10 axioms: +1 (Aligned), 0 (Neutral), -1 (Conflicts)
2. Write a brief note for each non-zero score explaining the reasoning
3. For any -1 score, write a justification in the **Conflict Justifications** section
4. Calculate the net score and verify it meets the threshold (>= +4)
5. Verify hard-fail rules:
   - No more than 2 axioms score -1
   - EARNED TRUST is not -1 if feature involves user-facing data
   - SECURE BY CONSTRUCTION is not -1 if feature introduces new data access

If the net score is below +4 or hard-fail rules are violated, **redesign before proceeding**. Discuss tradeoffs with the user and adjust the design to improve alignment.

**5b. Standards Compliance Check (MANDATORY)**

Before defining any schema, format, protocol, or data model, check whether an established standard already exists. **Do not invent custom formats when open standards apply** (Axiom #6: Protocol Over Custom).

**Checklist:**
- [ ] Search for existing standards in the domain (specs, RFCs, open protocols)
- [ ] Check ADK docs (`adk.dev`) for native support (skills, agents, tools, sessions)
- [ ] Check Agent Skills spec (`agentskills.io/specification`) for skill definitions
- [ ] Check protocol standards: AG-UI, A2UI, MCP, A2A
- [ ] If using Claude Code patterns, check existing SKILL.md conventions
- [ ] If a standard exists: adopt it, extend via metadata — do not reinvent
- [ ] **If the design touches a custom↔ADK seam** (a callback, a tool result, the model layer, the AG-UI stream, a handoff), check [`adk-contract-checklist.md`](../../../docs/design/v6.17.0/adk-contract-checklist.md). Conform to ADK's control-flow contracts (turn-finality, per-member model stamping, awaited coroutines, tracker binding) rather than building a parallel mechanism — that seam is where the recurring bugs live. Any new such seam owes a hermetic "real ADK flow" guard (`make adk-conformance`).

**Key standards for this project:**
- **Skill definitions**: Agent Skills spec (`SKILL.md` format) + ADK `load_skill_from_dir()` / `SkillToolset`
- **Agent config**: ADK `LlmAgentConfig` YAML schema + `from_config()`
- **Streaming**: AG-UI protocol events
- **UI rendering**: A2UI declarative JSON
- **Tool integration**: MCP standard
- **Agent discovery**: A2A agent cards

**If the design invents a custom format where a standard exists, it must score -1 on Axiom #6 and provide a written justification for why the standard is insufficient.**

**5b-bis. Consider CLI Affordances (MANDATORY for any feature with developer-facing surface)**

The v6 platform ships a local-dev CLI (`aitana`, see [local-dev-cli.md](../../../docs/design/v6.1.0/local-dev-cli.md)) that hosts every developer touchpoint. **Whenever you write a design doc, ask: does this feature need a CLI command to be ergonomic?** If yes, scope that command in the same doc so it ships with the feature, not as a follow-up.

**Heuristic — add a CLI command when the feature involves any of:**
- A new resource type that gets created/listed/updated/deleted by developers (e.g., skills, MCP servers, datastores, evalsets, channels) — typically `aitana <resource> new/list/get/push/pull/diff`
- A new local process developers will run (e.g., a sidecar service) — add to `cli/services.yaml` so `aitana dev up` starts it
- A new file format developers parse or generate locally (e.g., a new document type) — `aitana <subsystem> parse <file>` for a quick local check
- A new external system to inspect (e.g., MCP servers, A2A endpoints, BigQuery tables) — `aitana <subsystem> probe/list/call`
- A new evaluation or smoke-test target — `aitana eval run <name>`
- Anything currently requiring a curl + a Firebase token + JSON-by-hand to test — replace with a typed CLI command

**What to include in the design doc when a CLI command is in scope:**
- A small **"CLI Surface"** subsection under **Design** listing the new commands, their flags, and their position in the existing `aitana` command tree
- A line item in the **Implementation Plan** for the CLI work (typically 0.1-0.25 day per command — Click subcommand + an httpx call + a unit test)
- A line in **Success Criteria** asserting the new command works end-to-end
- A backlink to [local-dev-cli.md](../../../docs/design/v6.1.0/local-dev-cli.md) under **Related Documents**
- Update `cli/services.yaml` if the feature adds a local process

**When to skip:**
- Pure frontend features with no developer-facing API (e.g., a CSS refactor, a Tailwind theme tweak)
- Features whose only "config" is a code change (not a runtime resource)
- One-off bug fixes

**The anti-pattern:** shipping a feature, then six weeks later writing a follow-up design doc to "add CLI support". By then, three other features have shipped without CLI support either, and the dev loop has decayed to "open three terminals and curl with Firebase tokens". Bake the command into the original doc.

**5c. Verify API and Technical Details Against Authoritative Sources (MANDATORY)**

Design docs that include code examples, API usage, or integration patterns MUST verify those details against authoritative sources. **Do not write API calls, class names, or function signatures from memory** — LLM training data goes stale and APIs change frequently.

**Verification sources (in priority order):**

| Domain | Source | How to Access |
|--------|--------|---------------|
| **ADK (agents, tools, sessions)** | ADK MCP server | `mcp__adk-mcp__search_code` (search by class/function name), `mcp__adk-mcp__read_docs` (search by topic) |
| **ADK (skills)** | adk.dev | `WebFetch` on `https://adk.dev/skills/` (MCP server doesn't cover skills yet) |
| **ADK (skills, deploy, eval)** | ADK skills | `/adk-cheatsheet`, `/adk-dev-guide`, `/adk-deploy-guide`, `/adk-eval-guide`, `/adk-scaffold` |
| **Agent Skills spec** | agentskills.io | `WebFetch` on `https://agentskills.io/specification` |
| **AG-UI protocol** | ag-ui.com | `WebFetch` on `https://ag-ui.com/` or `https://docs.ag-ui.com/` |
| **A2UI protocol** | a2ui.org | `WebFetch` on `https://a2ui.org/` |
| **MCP protocol** | modelcontextprotocol.io | `WebFetch` on `https://modelcontextprotocol.io/docs/` |
| **A2A protocol** | a2a-protocol.org | `WebFetch` on `https://a2a-protocol.org/latest/specification/` |
| **CopilotKit (AG-UI React client)** | docs.copilotkit.ai | `WebFetch` on `https://docs.copilotkit.ai/` |
| **Firebase (Auth, Firestore)** | firebase.google.com | `WebFetch` on `https://firebase.google.com/docs/` or `mcp__google-dev-knowledge__search_documents` |
| **OpenTelemetry** | opentelemetry.io | `WebFetch` on `https://opentelemetry.io/docs/` |
| **Langfuse (observability)** | langfuse.com | `WebFetch` on `https://langfuse.com/docs` |
| **AILANG Parse** | PyPI | `WebFetch` on `https://pypi.org/project/ailang-parse/` |
| **Google Cloud (Cloud Run, GCS, etc.)** | Google Dev Knowledge MCP | `mcp__google-dev-knowledge__search_documents` |
| **NPM packages** | npm registry | `WebFetch` on `https://www.npmjs.com/package/<pkg>` (verify package exists) |
| **Python packages** | PyPI | `WebFetch` on `https://pypi.org/project/<pkg>/` (verify version) |

**Verification checklist for code examples in design docs:**
- [ ] Every class/function name verified against current source (not recalled from memory)
- [ ] Import paths checked (e.g., `google.adk.agents.Agent` not `google.adk.Agent`)
- [ ] Constructor/method signatures match current API version
- [ ] Callback signatures verified (ADK changes these between versions)
- [ ] Package versions in `pyproject.toml` / `package.json` confirmed to exist
- [ ] If an API is experimental/unstable, note the minimum version required

**Common pitfalls to avoid:**
- ADK API changes rapidly — `FunctionTool`, callback signatures, `Runner` API, and `SessionService` all differ between versions. Always verify via `mcp__adk-mcp__search_code`.
- NPM package names for A2UI, AG-UI, MCP Apps may not exist yet or may use different names than expected. Verify on npm before including in design docs.
- ADK `SkillToolset` is experimental (v1.25.0+) — note version requirements.

**6. Update SEQUENCE.md**

After writing the doc, add it to the version's `SEQUENCE.md` ordering table. Every design doc must have an entry — this is how the build order stays coherent.

- Find the right version's `docs/design/v<version>/SEQUENCE.md`
- Add a row to the **Ordering** table: order number, doc link, priority, estimate, dependencies, notes
- Add a row to the **Timeline estimate** table
- Add a line to **What ships in vX.Y.Z**
- Update the **Dependency Graph** if the doc has non-trivial dependencies
- If the doc is already done (moved to `implemented/`), mark it ✅ in the timeline row

**6. Review and Commit**

```bash
git add docs/design/<version>/feature-name.md docs/design/<version>/SEQUENCE.md
git commit -m "Add design doc for feature-name"
```

### Moving to Implemented

**When to move:**
- Feature is complete and deployed
- Tests are passing
- Documentation is updated

```bash
.claude/skills/design-doc-creator/scripts/move_to_implemented.sh feature-name
```

The script:
- Finds the doc under `docs/design/<version>/<name>.md`
- Moves it (with `git mv` when tracked) to `docs/design/<version>/implemented/<name>.md`
- Moves the companion `<name>-sprint.md` too if present
- Flips `**Status**: Planned|Proposed` → `Implemented` in the moved file
- Refreshes `**Last Updated**` to today
- Appends an Implementation Report stub to the design doc (sprint plans skipped)

## Naming Conventions

- Use lowercase with hyphens: `feature-name.md`
- For milestone features: `m-XXX-feature-name.md`
- Be specific: `slack-channel-integration.md` not `integration.md`

## Design Doc Locations

```
docs/design/
  planned/          # Active design docs being worked on
    v6.0.0/         # Organized by version (MAJOR.MINOR.PATCH)
    v6.1.0/
  implemented/      # Completed features
    v5.0.0/
    v6.0.0/
```

## Versioning

Design docs are organized by product version, not by date:
- **MAJOR** (6.x.x): Breaking changes, major architecture shifts
- **MINOR** (6.1.x): New features, significant enhancements
- **PATCH** (6.0.1): Bug fixes, small improvements

Ask the user which version the feature targets if not obvious. Current version: **v6.0.0**.

## Notes

- All design docs follow the template in `templates/design-doc.md`
- Keep design docs focused - split large features into multiple docs
- Update the design doc as reality diverges from plan
- Link design docs from sprint plans when they exist
