# Generated Document Outputs — ship the offer creator ONE already built

**Status**: Planned
**Priority**: P1 (Medium) — highest-leverage feature request of the UAT; the customer has already done most of the work
**Estimated**: ~3 days (Track A ~1d, Track B ~1d, Track C ~1d)
**Scope**: Fullstack (skill config, code-executor tooling, workbench download)
**Dependencies**: Dana to send her LaTeX offer-creator code, PowerPoint master template, and common BigQuery queries. **Blocked on that hand-off.**
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

The most valuable thing in the 2026-08-06 UAT was not a bug — it was Dana
describing three things ONE has already built by hand, each of which is a skill
we are one integration away from shipping.

**1. The offer creator (built, working, manual).**

> "We have the offer creator that long-stream did — we have the prompt of the new
> offer, an example of an offer in PDF, and the LaTeX code that Aitana has to
> fulfil... **Right now, we send to Aitana all this stuff and she outputs the
> LaTeX code filled with the information, and we have to go to LaTeX Online and
> print that to have the PDF.**"

Her question was simply whether that could be a click in the workspace. The
intelligence already works; the last mile is a rendering step the user performs
by hand, in a third-party web tool, on customer commercial data.

She also flagged a design question worth answering rather than assuming:

> "I did this because at the beginning Aitana wasn't able to create Word
> documents, or at least not with the format that I wanted. So I don't know if
> now it's optimal to use the LaTeX code — which works super well for the
> format — or if it's better to produce directly a Word document."

**2. PowerPoint from their own templates.**

> "I tried to do the same for PowerPoint and then it didn't work, but I had
> already made all the master slides."

> "It would be nice to have a repo with presentations we have made, so Aitana
> has access to that and knows the format and style of presentations we have."

**3. A BigQuery query library.**

> "I can give you for example a set of queries that are the most common ones, so
> you can have them in the Python code saved somewhere, so the assistant can
> just go to that query."

Mark's answer in the meeting is the right architecture and is already proven —
the Rockwool deployment does exactly this with the code executor:

> "It would take all that context, take it to the code executor box, install a
> Python library which is good for creating PowerPoints, and then just do it...
> It was making Excel sheets and PowerPoints and Word documents."

**Impact:**
- **Who:** Dana and long-stream directly; the offer creator is a revenue-path
  workflow, not an internal convenience.
- **How significant:** high-leverage. The customer has done the hard part
  (templates, prompts, LaTeX, queries); we are integrating, not inventing.
- **Also a security fix:** today the LaTeX round-trip takes ONE's commercial
  offer content through **latexonline (a third-party web service)**. That is an
  egress of customer-confidential content outside the GCP project edge. Bringing
  the render in-house removes it. This alone justifies Track A.

## Goals

**Primary Goal:** A user asks for an offer, a deck or a report and receives a
finished, correctly formatted file in the workbench — no third-party tools, no
copy-paste, no manual render step.

**Success Metrics:**
- The offer creator runs end-to-end in-app and yields a PDF matching the current
  hand-made output (long-stream's judgement is the acceptance test).
- Zero customer content sent to latexonline or any third-party renderer.
- A generated deck uses ONE's master template.
- Common BigQuery queries are callable by name rather than re-derived per session.

**Non-Goals:**
- A general document-authoring suite. Three concrete named workflows.
- Replacing the code executor with bespoke generators — the executor *is* the
  mechanism (Axiom #3: skills, not features).
- Choosing LaTeX vs Word by assumption. That is a Track A deliverable, decided
  by output quality against long-stream's template.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Document generation is inherently multi-second; must be visibly staged (principle #8), not fast. |
| 2 | EARNED TRUST | +1 | Output matches the customer's own approved template rather than an invented format. |
| 3 | SKILLS, NOT FEATURES | +1 | Ships as skills composed from existing tools; no new bespoke subsystem. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Chaining generation work — per `backend/adk/CLAUDE.md` finding #7, these are `pro`-tier skills, not front doors. |
| 5 | GRACEFUL DEGRADATION | +1 | A failed render returns the source (LaTeX/pptx script) plus the error, so the user is never worse off than today's manual path. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Results land as A2UI workbench artifacts via a registered mapping — no per-tool React. |
| 7 | API FIRST | +1 | Generation is a tool + endpoint; the CLI can drive it. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Long-running generation must stream stage progress into Activity. |
| 9 | SECURE BY CONSTRUCTION | +1 | **Removes** an existing third-party egress of confidential commercial content. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client renders a download artifact; all generation server-side. |
| | **Net Score** | **+9** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Three tracks sharing one mechanism: the ADK code executor generates a file from
a template plus model-supplied content; the file is stored as an artifact; the
workbench renders a download surface via a registered A2UI mapping.

### Track A — Offer creator (LaTeX → PDF)

- Port long-stream's LaTeX template and prompt into a skill (`one-offer-creator`).
- Install a TeX distribution in the code-executor image (Mark: *"we could install
  LaTeX on the server"*). Confirm image size impact — a full TeXLive is large;
  prefer a minimal distribution plus the packages the template needs.
- The skill fills the template, renders to PDF in-executor, saves the artifact.
- **Decide LaTeX vs `.docx` on evidence.** Dana asked directly. Recommendation:
  keep LaTeX for Track A because her template already produces the exact output
  long-stream signs off, and switching formats risks the fidelity that made it work.
  Evaluate `python-docx` in parallel and revisit only if it matches on quality —
  editability after generation is a real advantage worth testing for.
- On render failure, return the LaTeX source and the compiler error. The user is
  then exactly where they are today (paste into a renderer), never worse.

### Track B — PowerPoint from ONE's templates

- Store ONE's master template in their tagged bucket; the skill points at that
  folder (Mark: *"point it at a folder that contains your PowerPoint templates"*).
- Code executor uses `python-pptx` against the master, as proven at Rockwool.
- Note Dana's earlier attempt failed — capture *why* during hand-off; her master
  slides exist, so the gap is likely template-binding, not capability.

### Track C — BigQuery query library

- Dana's common queries become named, parameterised entries the skill can call
  by name — extending the existing BigQuery connection rather than a new tool.
- Per `backend/adk/CLAUDE.md` finding #1, parameters must be **optional with an
  elicitation envelope**, and the docstring must tell the agent to call bare
  rather than invent values. The `entsoe_day_ahead_prices` bug (model invented
  a year and reported it) is the exact failure mode to avoid here.

### Shared: output surface

Generated files land as workbench artifacts with a registered result→A2UI
mapping (`kind: "document"`), giving a download affordance, a title and a tab
for free — per [`backend/adk/CLAUDE.md`](../../../backend/adk/CLAUDE.md). Mark
this tool render-payload so a large result is never offloaded (trap 4). Do not
write a bespoke React component for it.

Generation takes tens of seconds, so it **must** stream stage progress
("Filling template… Rendering… Done") into Activity. A silent 40-second wait is
a principle-#8 violation regardless of whether the output is correct.

### CLI Surface

```
aiplatform document generate --skill one-offer-creator --input offer.json
aiplatform bq query list                      # the named query library
```

## Implementation Plan

### Phase 0: Hand-off (BLOCKING — Dana)
- [ ] LaTeX offer template + prompt + example PDF
- [ ] PowerPoint master template(s) + notes on the failed attempt
- [ ] Common BigQuery queries

### Track A: Offer creator (~1 day)
- [ ] TeX distribution in the executor image; measure size/cold-start impact
- [ ] `one-offer-creator` skill from long-stream's template (`pro` tier)
- [ ] A2UI document-artifact mapping + download (~80 LOC)
- [ ] Failure path returns source + compiler error (~30 LOC)
- [ ] Side-by-side quality comparison vs `python-docx`; record the decision here

### Track B: PowerPoint (~1 day)
- [ ] Template folder binding in skill config
- [ ] `python-pptx` generation against the master
- [ ] Diagnose and document Dana's earlier failure

### Track C: BigQuery library (~1 day)
- [ ] Named parameterised queries, optional params + elicitation
- [ ] Docstring instructs bare calls; never invent parameter values
- [ ] `aiplatform bq query list`

## Migration & Rollout

**Database Migrations:** None. Templates live in ONE's tagged bucket.

**Feature Flags:** Ship to Dana only first — Mark offered exactly this:
*"I can make it available to just you, not the entire Acme Energy team."*
Per-user skill visibility already exists in the admin.

**Rollback Plan:** Unpublish the skill. The manual workflow still exists
unchanged, so a rollback costs nothing.

**Environment Variables:** Possibly a template-bucket path per skill config.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Template fill produces valid LaTeX for a representative offer
- [ ] Render failure returns source + error, not a silent empty result
- [ ] Generated artifact is offload-exempt and carries artifact metadata
- [ ] A named BigQuery query called with no arguments returns an elicitation form, never invented values

### Frontend Tests (Vitest)
- [ ] A document artifact renders a download affordance in the workbench
- [ ] Generation in flight shows staged progress; failure shows a visible error

### Manual Testing (the real bar)
- [ ] Generate a real offer; **long-stream confirms it matches her hand-made output**
- [ ] Generate a deck; Dana confirms it uses ONE's template
- [ ] Confirm zero requests to latexonline or any third-party renderer

## Security Considerations

**This track removes an existing leak path.** Today ONE's commercial offer
content is pasted into latexonline — a third-party web service outside the GCP
project edge, in direct tension with CLAUDE.md's confidentiality rule and Axiom
#9. In-house rendering ends that.

Generated documents are customer-confidential and must be served behind the same
auth gate as their source data: artifact download goes through an authenticated
backend route with an ownership/group-tag check, never a public GCS URL. **A
generated offer PDF in a public bucket is precisely the leak CLAUDE.md's security
rule exists to prevent.** Templates in the ONE bucket keep their tagged-access
policy.

## Performance Considerations

Generation is tens of seconds — acceptable for this workflow, but it must be
staged and observable, and must not block the chat turn. A TeX distribution
materially increases the executor image size; measure the cold-start cost before
committing, and consider a dedicated executor image if it regresses interactive
code execution.

## Success Criteria

- [ ] Backend and frontend tests passing
- [ ] Offer creator produces a PDF long-stream accepts
- [ ] Deck generation uses ONE's master template
- [ ] Named BigQuery queries callable, with no invented parameters
- [ ] No customer content reaches any third-party renderer
- [ ] Downloads served behind auth, never a public URL
- [ ] Dana has it before 1 Sept

## Open Questions

- **LaTeX or `.docx` for the offer?** Track A decides on output quality, not
  preference. Editability may tip it; fidelity to long-stream's template outranks that.
- **Why did Dana's PowerPoint attempt fail?** Answer during hand-off — it likely
  determines whether Track B is a day or an hour.
- **Executor image size.** Does TeX warrant a separate image?
- **Does the "intelligent Aitana" Python code Dana mentioned overlap Track C?**
  She offered it; scope once received.

## Related Documents

- UAT source record (internal notes)
- [`backend/adk/CLAUDE.md`](../../../backend/adk/CLAUDE.md) — A2UI mapping, offload exemption, optional-params finding
- [tool-results-as-a2ui.md](../v6.7.0/implemented/tool-results-as-a2ui.md) — the artifact mapping model
