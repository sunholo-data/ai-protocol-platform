# Bucket Browser Pagination + Doc-Compare File Access

**Status**: Implemented (2026-06-19, sprint BUCKET-FILES)
**Priority**: P1

> **As-built (2026-06-19):** Both shipped + deployed to dev (frontend-only).
> A — DocCompareShell empty state now renders the examples picker + ONE bucket
> library (wired to import-by-reference). B — GCSFileBrowser "Load more" threads
> the backend's `nextPageToken`. 9 vitest; frontend deploy SUCCESS + smoke green.
>
> **Follow-up shipped (2026-06-19, per customer expectation):** picked docs are
> now collected as removable chips; selecting **two** reveals a "Compare these
> two →" button that runs the comparison. Crucially this travels via
> `forwardedProps.document_ids` ONLY (the **artifact path** — `make_document_loader`
> saves each as a `doc:{id}.json` artifact the compare tool reads selectively);
> `resumedSession` is left unset so the eager full-document inline is NOT
> triggered. This keeps 60–137pp PPAs out of the prompt — the user's explicit
> "dangerous to dump all in" constraint. Dedup by doc id; "exactly two" enforced.
> 2 added vitest. Scope: doc-compare flow only (chat-primary keeps its existing
> doc-tab context behaviour).
>
> Still deferred: list virtualization (Q1). DOM glance left to a signed-in
> browser (no Chrome connected to the agent).
**Estimated**: ~2 days
**Scope**: Frontend (one shared component + one shell; no backend changes)
**Dependencies**: v6.4.0 SHELL-MODES (DocCompareShell) ✅; v6.4.0 4.5 SKILL-ONBOARDING (GCSFileBrowser, SkillExamplesPicker, import-by-reference) ✅; backend `/api/buckets/{name}/list` already paginates ✅
**Created**: 2026-06-19
**Last Updated**: 2026-06-19

## Problem Statement

Customer feedback (2026-06-19) raised two file-browsing issues.

**A — Doc-compare lost its file browser.** When `one-doc-compare` was flipped to `shell.mode: doc-compare` (SHELL-MODES sprint), `DocCompareShell` was built without a sidebar — but the skill's `welcome.bucket_browser` (the Contracts library) and `welcome.example_documents` (the example picker) **only render inside `ChatShell`'s sidebar / Workbench** ([ChatShell.tsx:747](../../../frontend/src/components/chat/ChatShell.tsx#L747)). So a doc-compare user has **no way to browse the ONE bucket or pick example contracts** — the exact "pick two PPAs" workflow the skill exists for. They can only upload or paste via chat.

**B — Large folders truncate silently.** The backend `/api/buckets/{name}/list` paginates correctly (folder-scoped `delimiter="/"`, `prefix`, `max_results`, `nextPageToken`) — a real improvement over v5, which lagged/errored listing whole buckets. But the frontend [`GCSFileBrowser`](../../../frontend/src/components/doc-browser/GCSFileBrowser.tsx) requests `limit=100` and **ignores `nextPageToken`** — a folder with >100 files shows only the first 100, with no indication more exist.

**Impact:**
- A: doc-compare is the ONE consultant's headline workflow; missing file access is a functional regression from the shell flip. **High.**
- B: silent truncation on large folders — wrong/missing data with no signal. Medium (depends on per-folder file counts).

## Goals

**Primary Goal:** Restore bucket + example-document access in doc-compare mode, and make `GCSFileBrowser` fully browse folders of any size.

**Success Metrics:**
- In `DocCompareShell`, before a comparison is rendered, the user can pick an example PPA or browse the ONE bucket and load a contract (via the same import-by-reference path uploads use).
- A folder with >100 files is fully browsable via "Load more" (no silent truncation); a "Load more" control appears iff `nextPageToken` is present.

**Non-Goals:**
- List virtualization (deferred — note it; folders are modest today and "Load more" appends).
- Backend changes (pagination already exists server-side).
- Re-adding a full sidebar to `DocCompareShell` (the file affordance lives in the empty workspace state, not a permanent rail).
- Rebuilding the compare-trigger flow (user still asks "compare these" in the chat drawer; this just gets the docs in).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Folder-scoped listing already fast; "Load more" keeps each page small. Doc-compare users reach their files in one click instead of not at all. |
| 2 | EARNED TRUST | +1 | Eliminates silent truncation — the user sees that more files exist and can load them; no "where are my files?" confusion. |
| 3 | SKILLS, NOT FEATURES | +1 | The doc-compare file affordance is driven by the skill's existing `welcome.{example_documents,bucket_browser}` config — no per-skill code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model change. |
| 5 | GRACEFUL DEGRADATION | +2 | "Load more" only appears when `nextPageToken` exists; doc-compare file panel only renders when `welcome` config is set and before a workspace surface arrives; falls back to today's empty prompt otherwise. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Reuses existing list endpoint + import-by-reference. |
| 7 | API FIRST | 0 | No new endpoint. |
| 8 | OBSERVABLE BY DEFAULT | 0 | No new telemetry. |
| 9 | SECURE BY CONSTRUCTION | 0 | Same SA-credentialed, auth-gated list endpoint; no new access path. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client just threads the server's `nextPageToken` back; pagination logic stays server-side. |
| | **Net Score** | **+7** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### B — GCSFileBrowser "Load more" (do first; reused by A)

- Add `nextPageToken` state. The fetch keeps `prefix` + `limit=100` and, when loading a next page, passes `&pageToken=<token>`.
- First page (or prefix change) **replaces** `entries`; "Load more" **appends**.
- Render a "Load more" button iff `nextPageToken` is non-null; show a small spinner while the next page loads.
- Reset token on prefix navigation (folder change / breadcrumb climb).
- Virtualization deferred (noted); a `log`-style comment documents the choice.

### A — DocCompareShell file access

`DocCompareShell`'s `WorkspaceCanvas` currently shows a bare prompt when no workspace surface is present. Replace that empty state with the onboarding affordances `ChatShell` already gives chat-primary skills:

- When `welcome.example_documents` is set → `SkillExamplesPicker` (pick an example PPA).
- When `welcome.bucket_browser` is set → `GCSFileBrowser` (browse the ONE bucket).
- Both wired to a shared `onImport(bucket, object)` → `importByReference(bucket, object, skillId)` (the same lib helper ChatShell uses) so picked docs parse + land in context.
- Once the agent renders a comparison to the **workspace** surface, that surface takes over (the file panel is the *pre-comparison* state only).
- The chat drawer remains the place the user says "compare these two".

No sidebar is re-added; the affordance lives in the canvas's empty state, which is exactly when the user needs to choose documents.

### Files

- `frontend/src/components/doc-browser/GCSFileBrowser.tsx` — add pagination (B).
- `frontend/src/components/shells/DocCompareShell.tsx` — file-access empty state (A); add `skillId` is already present; import `SkillExamplesPicker`, `GCSFileBrowser`, `importByReference`.
- Tests: `GCSFileBrowser` pagination; `DocCompareShell` empty-state picker/browser + import wiring.

### API Changes

None — `/api/buckets/{name}/list` already returns `nextPageToken`; `/api/documents/import-by-reference` already exists.

## Implementation Plan

### M1 — GCSFileBrowser "Load more" (frontend, ~0.5d)
- [ ] `nextPageToken` state; append-on-load-more, replace-on-prefix-change; reset on navigation (~50 LOC)
- [ ] "Load more" button gated on token; loading state
- [ ] vitest: first page renders + token→button shown; load-more appends; no token→no button; prefix change resets

### M2 — DocCompareShell file access (frontend, ~1d)
- [ ] `WorkspaceCanvas` empty state renders `SkillExamplesPicker` (when examples) + `GCSFileBrowser` (when bucket_browser), wired to `importByReference` (~120 LOC)
- [ ] Picked doc imports + becomes context; once workspace surface present, file panel is replaced by the comparison
- [ ] vitest: empty state shows picker+browser when welcome set; hidden once surface present; pick → importByReference called; no-welcome → today's prompt fallback

### M3 — Deploy + verify (~0.25d)
- [ ] Push (frontend deploy); chrome-devtools/API verify: doc-compare shows the ONE library + examples pre-comparison; a folder >100 files exposes "Load more"

## Migration & Rollout
**DB / flags / env:** none. Pure frontend. **Rollback:** revert the two component changes; behaviour returns to today's (no doc-compare file panel; first-100-only listing).

## Testing Strategy
- **Vitest:** GCSFileBrowser pagination (4 cases); DocCompareShell empty-state file access (4 cases).
- **Manual/E2E (chrome-devtools):** doc-compare pre-comparison shows examples + ONE bucket; pick → doc loads; large folder → "Load more" works; chat-primary skills (one-ppa-expert) unaffected.

## Security Considerations
Same auth-gated, SA-credentialed list + import endpoints. No new access path. Doc-compare only browses the bucket the skill's own `welcome.bucket_browser` declares.

## Performance Considerations
"Load more" keeps each request at ≤100 entries (server cap 500). No virtualization yet — acceptable for current folder sizes; revisit if a folder routinely exceeds a few hundred loaded rows.

## Success Criteria
- [ ] Doc-compare users can pick example PPAs / browse the ONE bucket before a comparison; picked docs load via import-by-reference
- [ ] Folders >100 files fully browsable via "Load more"; control hidden when no more pages
- [ ] one-ppa-expert (chat-primary) sidebar browser unchanged
- [ ] Frontend tests + lint + typecheck + build green; live-verified on dev

## Open Questions
- **Q1 — Virtualization.** Deferred. Revisit if "Load more" lets a folder accumulate enough rows to feel heavy.
- **Q2 — Should doc-compare auto-trigger the comparison once two docs are picked**, or wait for the user to ask in chat? v1: wait for the chat ask (no magic). Revisit with the customer.

## Related Documents
- [skill-driven-shell-modes.md](skill-driven-shell-modes.md) — introduced DocCompareShell (the no-sidebar shell this fixes).
- [skill-onboarding.md](../v6.4.0/skill-onboarding.md) — GCSFileBrowser, SkillExamplesPicker, welcome config.
- [document-import-by-reference.md](../v6.4.0/document-import-by-reference.md) — the import path picked files use.
