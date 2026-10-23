# User guide — how to write a page

These pages are rendered in the app at `/help/<slug>`. They are also the knowledge base of the `platform-guide` skill,
which answers "how do I…?" questions by citing them. Design:
`docs/design/private/user-guide-and-platform-guide-skill.md` (in platform-source).

## One file per task area

`<slug>.md` covers one area (folders, skills, chat…), not one screen. Front matter:

```yaml
---
title: Working with folders           # page title (H1 is generated from it)
summary: One sentence for the index card and search snippets.
audience: user                         # user | owner | admin
order: 20                              # position in the /help index
requires: [knowledge_bases]            # optional: feature keys the page depends on
surfaces: [frontend/src/components/doc-browser/]   # code this page documents (staleness check)
reviewed: 2026-10-16
---
```

## Rules (checked by `make guide-check`)

- **Each `##` heading is one task.** It becomes the anchor (`#make-a-folder-searchable`) and one entry in the knowledge
  base, so a citation opens exactly that task.
- **A section that ends in images shows them beside its steps.** End it with one or two images. The image title is the
  caption: `![alt text](img/<slug>/name.png "Caption.")`.
- **Alt text is required.** Describe what the screenshot shows; the knowledge base indexes it.
- **UI labels are bold and exact**, e.g. `**Make searchable**`. The check confirms each label exists in `frontend/src`, so
  a renamed button fails the build instead of silently making the page wrong.
- **Callouts** use GitHub alerts: `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`.
- **Keys and paths** go in backticks: `` `Enter` ``, `` `Quarterly reports/2026/` ``.
- **Brand-neutral.** Write `{{appName}}`, never a product or customer name. Customer-specific notes go in an overlay.
- **Screenshots use synthetic data only**, produced by `make guide-shots` against the local app with the API mocked.
  Never capture a deployed environment.

## Customer overlays (forks)

A fork adds `docs/customers/<customer>/user-guide/`:

- `guide.yaml`: `tags: [...]`, the group tags that may use the guide (the `platform-guide` KB and skill).
- `<slug>.md`: a new page, or one that replaces the platform page with the same slug.
- A file with `extends: <slug>` and `after: <anchor>` in its front matter appends its body to the end of that section.

`node scripts/user-guide/bundle.mjs` merges everything into `frontend/content/user-guide/` and
`backend/bundled/user-guide/`. Both are build outputs, so never edit them.
