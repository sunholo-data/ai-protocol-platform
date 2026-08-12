# v5 Data Migration

**Status**: Planned
**Priority**: P2 (Low)
**Estimated**: 3 days
**Scope**: Backend (scripts)
**Dependencies**: [Skills Data Model](skills-data-model.md)
**Created**: 2026-04-10
**Last Updated**: 2026-04-10

## Problem Statement

v5 stores assistants, messages, templates, and user data in Firestore. v6 uses a new `skills/` collection with a different schema. We need a migration script that transforms v5 data to v6 format without affecting v5 operations (both run against the same Firestore project).

**Current State:**
- v5 Firestore: `assistants/`, `assistants/{id}/messages/`, `templates/`
- v6 Firestore: `skills/` (empty, to be created)
- Same GCP project for both v5 and v6
- Firebase Auth shared (no user migration needed)
- `backend/scripts/` exists but is empty

**Impact:**
- Required for production cutover (existing users must see their assistants as skills)
- Must be idempotent (safe to re-run during parallel operation period)
- Must preserve traceability (v5AssistantId backlink)

## Goals

**Primary Goal:** Migrate all v5 assistant data to v6 skill format with zero data loss, full traceability, and idempotent execution.

**Success Metrics:**
- All v5 assistants appear as v6 skills with correct schema
- Message history preserved and accessible
- Migration is idempotent (re-run produces same result)
- v5 data untouched (non-destructive)
- `v5AssistantId` backlinks enable reverse lookup

**Non-Goals:**
- Real-time sync (migration is a one-time batch operation, run before cutover)
- Migrating Sunholo config files (dropped)
- Migrating tool orchestrator state (dropped)
- Migrating assistant-specific caching (dropped)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | One-time script, not on latency path |
| 2 | EARNED TRUST | 0 | No user-facing claims |
| 3 | SKILLS, NOT FEATURES | +1 | Converts assistants to skills — enforces the new abstraction |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Migration preserves existing model config |
| 5 | GRACEFUL DEGRADATION | +1 | Dry-run mode, idempotent, non-destructive — v5 data untouched |
| 6 | PROTOCOL OVER CUSTOM | 0 | Internal migration script |
| 7 | API FIRST | 0 | Backend script, not API |
| 8 | OBSERVABLE BY DEFAULT | +1 | Logs all actions, verification script, audit trail |
| 9 | SECURE BY CONSTRUCTION | +1 | SA credentials, no data deletion, dry-run by default |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend script |
| | **Net Score** | **+4** | Threshold: >= +4 |

## Design

### Overview

A Python script reads from v5 Firestore collections, transforms documents to v6 schema, and writes to v6 collections. The script is idempotent — it checks for existing v6 documents before writing and uses `v5AssistantId` as the deduplication key.

### Schema Transform

```
v5: assistants/{id}                    →  v6: skills/{new_id}
─────────────────────────────────────────────────────────────
id                                     →  skillId (new UUID)
name                                   →  name
description                            →  description
avatar                                 →  avatar
ownerEmail                             →  ownerEmail
ownerId                                →  ownerId
accessControl                          →  accessControl (same schema)
initialInstructions                    →  agent.instruction
model                                  →  agent.model
tools                                  →  agent.tools
toolConfigs                            →  agent.toolConfigs
subAssistants                          →  agent.subSkills (map assistant IDs to skill IDs)
initialMessage                         →  initialMessage
initialDocuments                       →  initialDocuments
tags                                   →  tags
createdAt                              →  createdAt
updatedAt                              →  updatedAt
(new)                                  →  v5AssistantId = id
(new)                                  →  protocols (defaults)
(new)                                  →  featured = false
(new)                                  →  usageCount = 0
```

### Message Migration

```
v5: assistants/{id}/messages/{msgId}   →  v6: skills/{skillId}/messages/{msgId}
─────────────────────────────────────────────────────────────
Schema is identical — copy directly.
```

### Template Migration

```
v5: templates/{id}                     →  v6: skills/{new_id} (as seed skills)
─────────────────────────────────────────────────────────────
Templates become regular skills with `featured: true`.
```

### Migration Script

```python
# backend/scripts/migrate_v5_to_v6.py

import uuid
from google.cloud import firestore

async def migrate(
    project_id: str,
    dry_run: bool = True,
    batch_size: int = 100,
):
    """
    Migrate v5 assistants to v6 skills.
    
    Args:
        project_id: GCP project ID
        dry_run: If True, log what would be migrated without writing
        batch_size: Number of documents per Firestore batch write
    """
    db = firestore.AsyncClient(project=project_id)
    
    # Track ID mapping for sub-assistant → sub-skill resolution
    id_map: dict[str, str] = {}  # v5_id → v6_id
    
    # Phase 1: Migrate assistants → skills
    print("Phase 1: Migrating assistants to skills...")
    assistants = db.collection("assistants").stream()
    
    async for doc in assistants:
        v5_data = doc.to_dict()
        v5_id = doc.id
        
        # Check if already migrated (idempotency)
        existing = await _find_by_v5_id(db, v5_id)
        if existing:
            id_map[v5_id] = existing.id
            print(f"  SKIP {v5_id} → {existing.id} (already migrated)")
            continue
        
        # Transform
        v6_id = str(uuid.uuid4())
        v6_data = transform_assistant_to_skill(v5_data, v6_id, v5_id)
        
        id_map[v5_id] = v6_id
        
        if dry_run:
            print(f"  DRY RUN: {v5_id} → {v6_id} ({v5_data.get('name', 'unnamed')})")
        else:
            await db.collection("skills").document(v6_id).set(v6_data)
            print(f"  MIGRATED: {v5_id} → {v6_id} ({v5_data.get('name', 'unnamed')})")
    
    # Phase 2: Resolve sub-skill references
    print("\nPhase 2: Resolving sub-skill references...")
    # Update agent.subSkills with mapped IDs
    
    # Phase 3: Migrate messages
    print("\nPhase 3: Migrating messages...")
    for v5_id, v6_id in id_map.items():
        messages = db.collection("assistants").document(v5_id).collection("messages").stream()
        batch = db.batch()
        count = 0
        async for msg_doc in messages:
            msg_ref = db.collection("skills").document(v6_id).collection("messages").document(msg_doc.id)
            batch.set(msg_ref, msg_doc.to_dict())
            count += 1
            if count % batch_size == 0:
                if not dry_run:
                    await batch.commit()
                batch = db.batch()
        if count % batch_size != 0 and not dry_run:
            await batch.commit()
        print(f"  {v5_id} → {v6_id}: {count} messages")
    
    # Phase 4: Migrate templates → featured skills
    print("\nPhase 4: Migrating templates...")
    # Similar to Phase 1 but with featured=True
    
    print(f"\nDone. Migrated {len(id_map)} assistants.")
    if dry_run:
        print("DRY RUN — no data was written. Run with --execute to apply.")


def transform_assistant_to_skill(v5: dict, v6_id: str, v5_id: str) -> dict:
    """Transform a v5 assistant document to v6 skill format."""
    return {
        "skillId": v6_id,
        "name": v5.get("name", ""),
        "description": v5.get("description", ""),
        "avatar": v5.get("avatar", ""),
        "ownerEmail": v5.get("ownerEmail", ""),
        "ownerId": v5.get("ownerId", ""),
        "accessControl": v5.get("accessControl", {"type": "private"}),
        "agent": {
            "model": v5.get("model", "gemini-2.5-flash"),
            "thinkingModel": None,
            "instruction": v5.get("initialInstructions", ""),
            "tools": v5.get("tools", []),
            "toolConfigs": v5.get("toolConfigs", {}),
            "subSkills": v5.get("subAssistants", []),  # Will be remapped in Phase 2
        },
        "protocols": {
            "mcp": {"enabled": False},
            "a2a": {"enabled": False},
            "agui": {"enabled": True},
            "a2ui": {"enabled": False},
            "mcpApps": {"enabled": False},
        },
        "initialMessage": v5.get("initialMessage", ""),
        "initialDocuments": v5.get("initialDocuments", []),
        "tags": v5.get("tags", []),
        "featured": False,
        "usageCount": 0,
        "createdAt": v5.get("createdAt", 0),
        "updatedAt": v5.get("updatedAt", 0),
        "v5AssistantId": v5_id,
    }


async def _find_by_v5_id(db, v5_id: str):
    """Find a skill by its v5AssistantId (idempotency check)."""
    query = db.collection("skills").where("v5AssistantId", "==", v5_id).limit(1)
    results = [doc async for doc in query.stream()]
    return results[0] if results else None
```

### CLI Interface

```bash
# Dry run (default — safe)
cd backend && uv run python scripts/migrate_v5_to_v6.py --project your-project-id

# Execute migration
cd backend && uv run python scripts/migrate_v5_to_v6.py --project your-project-id --execute

# Migrate specific environment
cd backend && uv run python scripts/migrate_v5_to_v6.py --project your-project-id-prod --execute
```

### Verification Script

```python
# backend/scripts/verify_migration.py

async def verify(project_id: str):
    """Verify migration completeness and data integrity."""
    db = firestore.AsyncClient(project=project_id)
    
    # Count v5 assistants
    v5_count = len([doc async for doc in db.collection("assistants").stream()])
    
    # Count v6 skills with v5AssistantId
    v6_migrated = len([doc async for doc in 
        db.collection("skills").where("v5AssistantId", "!=", None).stream()])
    
    print(f"v5 assistants: {v5_count}")
    print(f"v6 migrated skills: {v6_migrated}")
    print(f"Coverage: {v6_migrated}/{v5_count} ({100*v6_migrated/v5_count:.0f}%)")
    
    # Verify message counts match
    mismatches = []
    async for skill_doc in db.collection("skills").stream():
        skill = skill_doc.to_dict()
        v5_id = skill.get("v5AssistantId")
        if not v5_id:
            continue
        
        v5_msgs = len([m async for m in 
            db.collection("assistants").document(v5_id).collection("messages").stream()])
        v6_msgs = len([m async for m in 
            db.collection("skills").document(skill_doc.id).collection("messages").stream()])
        
        if v5_msgs != v6_msgs:
            mismatches.append((v5_id, skill_doc.id, v5_msgs, v6_msgs))
    
    if mismatches:
        print(f"\nMessage count mismatches: {len(mismatches)}")
        for v5_id, v6_id, v5_c, v6_c in mismatches:
            print(f"  {v5_id} → {v6_id}: v5={v5_c}, v6={v6_c}")
    else:
        print("\nAll message counts match.")
```

### Architecture Diagram

```
[v5 Firestore]                          [v6 Firestore]
    │                                        │
    ├── assistants/{id}          ──────►  skills/{new_id}
    │     ├── name               ──────►    ├── name
    │     ├── initialInstructions ─────►    ├── agent.instruction
    │     ├── model              ──────►    ├── agent.model
    │     ├── tools              ──────►    ├── agent.tools
    │     ├── subAssistants      ──────►    ├── agent.subSkills (remapped)
    │     └── ...                ──────►    ├── v5AssistantId = id
    │                                       └── protocols (defaults)
    │
    ├── assistants/{id}/messages  ─────►  skills/{new_id}/messages
    │     └── (copied verbatim)                └── (same schema)
    │
    └── templates/{id}           ─────►  skills/{new_id} (featured=true)
```

## Implementation Plan

### Phase 1: Migration Script (~1 day)
- [ ] Implement `backend/scripts/migrate_v5_to_v6.py`
- [ ] Transform function (assistant → skill)
- [ ] Idempotency check (v5AssistantId lookup)
- [ ] Sub-skill reference remapping
- [ ] Dry-run mode (default)
- [ ] Batch writes for messages

### Phase 2: Verification + Testing (~1 day)
- [ ] Implement `backend/scripts/verify_migration.py`
- [ ] Write unit tests for transform function
- [ ] Test idempotency (run twice, same result)
- [ ] Test on dev environment Firestore

### Phase 3: Production Migration (~1 day)
- [ ] Run dry-run on dev → verify output
- [ ] Execute on dev → verify with app
- [ ] Run dry-run on test → verify output
- [ ] Execute on test → verify with app
- [ ] Run dry-run on production → verify output
- [ ] Execute on production → verify with app
- [ ] Run verification script on all environments

## Migration & Rollout

**Execution Order:**
1. Dev environment first (safe to experiment)
2. Test environment (UAT)
3. Production (during maintenance window — but non-destructive, so no downtime)

**Parallel Operation:**
- v5 reads from `assistants/` — unaffected
- v6 reads from `skills/` — populated by migration
- Both can run simultaneously against same Firestore
- No conflict because they use different collections

**Rollback Plan:**
- Delete `skills/` collection (v5 `assistants/` is never modified)
- One command: `firebase firestore:delete skills --recursive --project <id>`

## Testing Strategy

### Unit Tests
- [ ] `transform_assistant_to_skill()` produces correct schema
- [ ] Missing optional fields use defaults
- [ ] Sub-assistant IDs remapped correctly
- [ ] Template → featured skill conversion

### Integration Tests
- [ ] Dry-run mode logs correctly without writing
- [ ] Execute mode creates skills in Firestore emulator
- [ ] Idempotency: second run creates no duplicates
- [ ] Message counts match after migration
- [ ] Verification script reports 100% coverage

## Security Considerations

- Script runs with service account credentials (not user token)
- No data deleted from v5 collections
- Script logs actions for audit trail
- Dry-run mode prevents accidental writes

## Performance Considerations

- Batch writes (100 per batch) to avoid Firestore rate limits
- Expected volume: <100 assistants, <10K messages total
- Full migration should complete in <5 minutes
- Message migration is the slowest part (sub-collection reads)

## Success Criteria

- [ ] All v5 assistants appear as v6 skills
- [ ] All messages migrated with correct counts
- [ ] Sub-skill references correctly remapped
- [ ] Templates migrated as featured skills
- [ ] Verification script reports 100% coverage
- [ ] Idempotent (re-run produces same result)
- [ ] v5 data completely untouched

## Open Questions

- Should the migration include a timestamp filter (only migrate assistants updated after X)?
- Should we migrate chat history or start fresh for v6?
- How to handle v5 assistants with tools that don't exist in v6 yet?

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Data migration section (lines 732-752)
- [Skills Data Model](skills-data-model.md) — Target schema
