# User guide — how to write a page

These pages are rendered in the app at `/help/<slug>`. They are also the knowledge base of the `platform-guide` skill,
which answers "how do I…?" questions by citing them. Design:
`docs/design/private/user-guide-and-platform-guide-skill.md` (in platform-source).

## One file per task area

`<slug>.md` covers one area (folders, skills, chat…), not one screen. These unprefixed files are the **source pages**,
written in English. A translation of a page lives in a folder named for its locale, `<locale>/<slug>.md` (see
[Translations](#translations)). Front matter:

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
- `<locale>/<slug>.md`: a translation, with the same rules applied within that locale (below).

`node scripts/user-guide/bundle.mjs` merges everything into `frontend/content/user-guide/` and
`backend/bundled/user-guide/`. Both are build outputs, so never edit them.

## Translations

A page can exist once per locale. Translations are for forks that offer more than one interface language
(`docs/integrations/interface-languages.md`):

```
docs/user-guide/<slug>.md                             source page (English)
docs/user-guide/<locale>/<slug>.md                    a translation in the template
docs/customers/<c>/user-guide/<locale>/<slug>.md      a translation in a fork overlay
```

- **The locale folder is a tag:** `da`, `de`, `pt-BR`. `img/` is never a locale.
- **A translation translates a source page.** Its slug must be a source page, the template's or the fork's own,
  or the bundle fails.
- **Same front matter**, except that `audience`, `order` and `requires` are inherited from the source page when
  omitted. `title` and `summary` are the translation's own.
- **Overlays work per locale.** A fork's `<locale>/<slug>.md` replaces the template's translation of that page,
  and an `extends:` file in `<locale>/` appends to that locale's page, using that page's (translated) anchors.
  An extension of the source page does not reach its translations: add one per locale.
- **Images are shared:** a translation uses the same `img/…` paths as the source page.
- **Headings are translated, so anchors are too.** Links within a translation use its own anchors; a link to
  another page uses that page's translation's anchors when it has one, else the source page's.
- **The bold-label check uses that locale's catalog** (`frontend/messages/<locale>/` and
  `docs/customers/*/messages/<locale>/`) plus the English interface, since an untranslated button still reads
  in English.

`/help/<slug>` shows the reader's language, falling back along the same chain as the interface (their choice →
the deployment default → English). The `platform-guide` knowledge base indexes each translation tagged with its
locale and cites the reader's language first. No translated pages ship in the template.
