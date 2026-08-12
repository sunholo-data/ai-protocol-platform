# Sprint Plan — Bucket Browser Pagination + Doc-Compare File Access (BUCKET-FILES)

**Design doc:** [bucket-browser-and-doc-compare-files.md](bucket-browser-and-doc-compare-files.md)
**Sprint key:** `BUCKET-FILES`
**Window:** ~2 days, single dev
**Scope:** Frontend only (no backend/API changes)
**Status:** Planned (2026-06-19)

## Goal
(A) Restore bucket + example-doc access in `DocCompareShell` (it dropped the sidebar, hiding `one-doc-compare`'s file library). (B) Make `GCSFileBrowser` browse folders of any size via "Load more" (backend already paginates; frontend ignored `nextPageToken`).

## Confirmed pre-conditions
- Backend `/api/buckets/{name}/list` already returns `nextPageToken` (folder-scoped, `limit` ≤ 500). No backend work.
- `importByReference(bucket, object, skillId)` lib helper + `/api/documents/import-by-reference` exist (used by ChatShell).
- `SkillExamplesPicker(examples, onPickExample)` + `GCSFileBrowser(bucket, rootPath, onPick)` exist.
- `DocCompareShell.WorkspaceCanvas` already calls `useSkillMeta` (has `welcome`); shows a bare prompt in the empty state today.

## Milestones

| M | Title | Scope | Est | Depends |
|---|-------|-------|-----|---------|
| M1 | GCSFileBrowser "Load more" pagination | frontend | ~0.5d | — |
| M2 | DocCompareShell pre-comparison file access | frontend | ~1d | M1 |
| M3 | Deploy + live verify | frontend/ops | ~0.25d | M1, M2 |

M1 first — A reuses the paginated browser. M3 is the deploy gate.

### M1 — GCSFileBrowser "Load more" (~0.5d)
- `nextPageToken` state; first-page/prefix-change **replaces** entries, "Load more" **appends**; token reset on folder navigation; fetch passes `&pageToken=` when loading next.
- "Load more" button gated on a non-null token + loading state.
- **Acceptance:** first page renders + token→button shown; load-more appends the next page; no token→no button; prefix change resets entries+token (4 vitest). quality:check green.

### M2 — DocCompareShell file access (~1d)
- `WorkspaceCanvas` empty state (no workspace surface yet): render `SkillExamplesPicker` when `welcome.example_documents` set + `GCSFileBrowser` when `welcome.bucket_browser` set, both wired to `importByReference(bucket, object, skillId)`.
- Once a workspace surface arrives, the comparison replaces the file panel (existing behaviour). No-welcome → today's prompt fallback.
- **Acceptance:** empty state shows picker+browser when welcome set; pick → importByReference called with right args; panel hidden once surface present; no-welcome → prompt fallback (4 vitest). quality:check green.

### M3 — Deploy + verify (~0.25d)
- Push (frontend deploy); chrome-devtools/API verify: doc-compare pre-comparison shows the ONE library + example PPAs and a pick loads a doc; a folder >100 files exposes "Load more"; one-ppa-expert sidebar browser unchanged.

## Out of scope
List virtualization (deferred); backend pagination (exists); re-adding a full doc-compare sidebar; auto-triggering the comparison on second pick.

## Quality gates
Per milestone `npm run quality:check:fast`; sprint close full `npm run quality:check` (incl. build) + chrome-devtools verify.

## Risks
- **Doc-compare empty-state ↔ surface transition:** ensure the file panel disappears cleanly once a comparison renders (gate on `useSurfaceState("workspace")`). Covered by M2 test.
- **"Load more" without virtualization:** acceptable at current folder sizes; noted for revisit.
