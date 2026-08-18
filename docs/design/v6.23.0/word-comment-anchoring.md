# Word Comment Anchoring — a comment without its anchor is noise

**Status**: Planned
**Priority**: P1 (Medium) — daily workflow for ONE's legal reviewer; the feature "works" today in a way that is worse than not working
**Estimated**: ~1.5 days (~1d in `ailang-parse`, ~0.5d platform-side)
**Scope**: Cross-repo — `ailang-parse` (docparse) primary, platform consumption secondary
**Dependencies**: Requires an `ailang-parse` release. Coordinate via the docparse inbox (`ailang messages send docparse`).
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

At the 2026-08-06 ONE UAT, Tomas made the sharpest technical point of the
meeting. We had just told him the good news — v6 parses Word documents in under
a second and, unlike PDFs, *can* read tracked comments. His reply:

> "The thing is, Mark, the comment attribute in Word is a piece of text which
> points towards a selection of text. **Not only the comment is important but
> also that it is able to read which piece of text is attached.** If a comment
> says 'this is nonsense', it's important which part of the text is nonsense."

> "Please check this, and we will check, because long-stream uses this every day."

He is describing the actual OOXML data model, correctly. And the current
implementation does exactly the half he warned about.

### Current state (verified in source, 2026-08-06)

`docparse/services/docx_parser.ail` reads `word/comments.xml` and emits each
`w:comment` as a block with the author prefixed:

```
-- Extract comments from a DOCX file (word/comments.xml)
-- Each w:comment becomes a SectionBlock(kind: "comment") with author-prefixed text
TextBlock({text: "[${author}] ${commentText}", style: "CommentText", level: 0})
```

The anchor is never read. In OOXML the comment *body* lives in
`word/comments.xml` keyed by `w:id`, while the anchored **range** lives in
`word/document.xml` as `w:commentRangeStart` / `w:commentRangeEnd` /
`w:commentReference` markers carrying the same `w:id`. Correlating the two is
the whole job, and we do not do it. Comments arrive as a detached list at the
end of the document with no indication of what they refer to.

### Why this is worse than not supporting comments

An agent handed `[long-stream] This is unacceptable, renegotiate` with no anchor will
do the most damage-prone thing available: guess which clause it refers to, from
proximity or topic. A confidently misattributed legal objection — the right
comment pinned to the wrong clause — is materially worse than "comments are not
supported", because the user has no signal that it was a guess. This is the same
failure shape as the `wrap_with_today` finding in
[`backend/adk/CLAUDE.md`](../../../backend/adk/CLAUDE.md): *a confidently wrong
answer is worse than a refusal, because it looks right.*

**Impact:**
- **Who:** long-stream (ONE's legal reviewer) daily, per Tomas. Every contract-review
  journey, which is the flagship use case.
- **How significant:** major friction, and a correctness hazard on the customer's
  highest-stakes workflow. Tomas explicitly asked for it and said they will test it.

## Goals

**Primary Goal:** A commented `.docx` parses such that every comment carries the
exact span of document text it annotates, and the agent quotes that span rather
than inferring one.

**Success Metrics:**
- 100% of anchored comments in a test corpus resolve to their correct text span
  (today: 0% — no anchors are extracted at all).
- An agent asked "what did long-stream object to?" quotes the anchored clause verbatim.
- Orphaned comments (anchor missing/malformed) are labelled as unanchored rather
  than silently attached to nearby text.

**Non-Goals:**
- Writing comments back into a `.docx`. Read-only for now.
- Tracked changes (`w:ins`/`w:del`) — related, separately valuable, separate work.
- PDF comment extraction. Tomas's own recommendation is to move to Word.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Deterministic XML correlation, no model call; parse stays sub-second. |
| 2 | EARNED TRUST | +1 | Removes a class of confident misattribution on legal content — the core of the fix. |
| 3 | SKILLS, NOT FEATURES | +1 | Lands as a capability every document skill inherits, not a per-skill feature. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model involved. |
| 5 | GRACEFUL DEGRADATION | +1 | Unanchored comments degrade to an explicit "unanchored" label instead of a silent guess. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Implements the OOXML comment-range model as specified rather than inventing an association heuristic. |
| 7 | API FIRST | 0 | Extends an existing parse contract. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Parser warnings surface orphaned anchors; no new telemetry. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access — same document, more of it read. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Entirely in the parse/backend layer; no client changes. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Correlate `w:id` between `word/document.xml` range markers and
`word/comments.xml` bodies during DOCX parse, and attach the anchored text span
to each comment block. Then make the platform's document context carry the
anchor so the agent sees comment-and-target as one unit.

### `ailang-parse` changes (primary)

In `docx_parser.ail`:

1. While walking `document.xml`, record `w:commentRangeStart` / `w:commentRangeEnd`
   positions keyed by `w:id`, and capture the run text between them.
2. When building comment blocks from `comments.xml`, join on `w:id` and attach:
   - `anchorText` — the exact annotated span
   - `anchorBlockIndex` — which content block the span sits in, so a consumer can
     render it in place
   - `anchored: bool` — false when no matching range exists
3. Emit comments **in document order at their anchor point**, in addition to the
   existing end-of-document list, so a linear reader encounters each comment
   beside the text it annotates.
4. A `w:commentReference` with no matching range is a point anchor, not an error —
   attach the containing paragraph and mark it as such.

Edge cases that must not silently mis-anchor: overlapping ranges, ranges
spanning table cells, ranges crossing paragraph boundaries, replies/threaded
comments (`w:parentId`, in `commentsExtended.xml`), and a `w:id` present in one
file but not the other. Each should degrade to `anchored: false` rather than
guess.

**This work lands in the `ailang-parse` repo, not here.** File it through the
docparse inbox (`ailang messages send docparse "…"`) per
[the feedback convention](../../../CLAUDE.md), and reference this doc.

### Platform changes (secondary)

Once the parser carries anchors:

- The document loader includes `anchorText` in the block payload it puts in
  agent context, formatted so the pairing is unambiguous — e.g.
  `[comment by long-stream on "…the Seller shall indemnify…"]: renegotiate this`.
- The document-analyst / PPA skills get an instruction line: *when citing a
  comment, quote its anchored text; if a comment is unanchored, say so rather
  than inferring a target.* Per the `backend/adk/CLAUDE.md` finding that
  data-shape fixes alone don't change model behaviour, **both halves are needed**.
- The Document tab renders anchored comments as margin notes against the span.
  Deferred to a follow-up unless it falls out cheaply.

### CLI Surface

```
aiplatform document comments <file.docx>    # list comments with anchors; the acceptance check for this feature
```

## Implementation Plan

### Phase 1: Parser anchoring (~1 day, `ailang-parse`)
- [ ] Collect `w:commentRangeStart`/`End` by `w:id` during document walk
- [ ] Join comment bodies to ranges; attach `anchorText`, `anchorBlockIndex`, `anchored`
- [ ] Emit comments in document order at their anchor
- [ ] Degrade unmatched anchors to `anchored: false` with a parser warning
- [ ] Fixture corpus: overlapping, table-spanning, threaded, orphaned

### Phase 2: Platform consumption (~0.5 day)
- [ ] Loader includes anchor text in agent context (~30 LOC)
- [ ] Skill instruction: quote the anchor; never infer one (~10 LOC)
- [ ] `aiplatform document comments` (~40 LOC)
- [ ] Test: a commented fixture yields comment+anchor pairs in context (~50 LOC)

## Migration & Rollout

**Database Migrations:** None. Re-parsing a document is idempotent; previously
parsed documents simply lack anchors until re-parsed.

**Feature Flags:** None needed — strictly additive fields.

**Rollback Plan:** Pin the previous `ailang-parse` version. Platform-side reads
are null-safe against the old shape.

**Environment Variables:** None.

## Testing Strategy

### Backend Tests (pytest)
- [ ] A commented fixture yields each comment with its correct `anchorText`
- [ ] An orphaned comment is marked `anchored: false` and is NOT attached to nearby text
- [ ] Threaded replies associate to the same anchor as their parent
- [ ] Loader context contains comment and anchor as one unit

### Manual Testing
- [ ] Parse a real ONE contract with long-stream's comments; spot-check every anchor
- [ ] Ask the agent "what did long-stream object to?" and confirm it quotes the anchored clause
- [ ] Confirm a document with zero comments is unchanged

## Security Considerations

Comments in customer contracts are among the most sensitive content in the
document — they carry counsel's candid assessment of a counterparty. They stay
inside the same access gate as the document body: never rendered into a public
artefact, never logged outside the GCP project edge, never included in a
thumbnail or preview (CLAUDE.md security rule).

## Performance Considerations

One extra pass over `document.xml` to collect range markers; no model calls.
The sub-second parse budget that makes Word preferable to PDF is preserved —
that speed advantage is the reason we are steering ONE to `.docx` at all.

## Success Criteria

- [ ] `ailang-parse` release with anchored comments
- [ ] Backend tests passing (`cd backend && make test-fast`)
- [ ] `aiplatform document comments` lists anchors for a real contract
- [ ] Agent quotes anchored text rather than guessing, verified live
- [ ] long-stream confirms the behaviour on a document she has actually annotated

## Open Questions

- **Tracked changes next?** `w:ins`/`w:del` is the same correlation problem and
  the same reviewer's workflow. Likely the immediate follow-up.
- **Should the Document tab render margin notes?** Genuinely useful, but the
  agent-context fix delivers most of the value; sequence after.
- **Do ONE's documents use threaded comment replies?** Changes how much
  `commentsExtended.xml` work is warranted. Ask long-stream directly.

## Related Documents

- UAT source record (internal notes)
- [`backend/adk/CLAUDE.md`](../../../backend/adk/CLAUDE.md) — why data-shape fixes need a paired instruction
- [conversation-context-fidelity.md](conversation-context-fidelity.md) — sibling P0 from the same session
