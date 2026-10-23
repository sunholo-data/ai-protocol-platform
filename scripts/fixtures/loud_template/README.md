# The loud template

A deliberately garish `.docx` for testing document restyling.

`make loud-template` builds `loud-template.docx` from the OOXML parts in
`parts/`. Upload it as a template and apply it to any document.

## Why it exists

The templates in real use are subtle — a letterhead, a corporate font — and
subtle is exactly wrong for a test fixture. On 2026-09-04 a restyle appeared to
do nothing, and it took a byte-level comparison of the produced `.docx` to
establish that it had in fact worked every time and the browser was redrawing a
cached copy of the old bytes. With a template you can identify from across the
room, that question answers itself.

Applied, you get:

| Signal | What you see |
|---|---|
| Page setup | **Landscape**, with a 2-inch left margin |
| Header / footer | Red-on-yellow banners that name themselves |
| Headings | White on magenta, Arial Black, all caps |
| Body | Courier New, indigo |
| Tables | Teal double borders, magenta inside lines |

Any one of those appearing means the template carried. None of them appearing
means it did not — no squinting required.

## How it is built

A `.docx` is a zip of XML parts, so this needs no document library: `build.ail`
reads `parts/` and writes the archive with `std/zip`. The parts are checked in
and the `.docx` is not — it is a build output.

To change the look, edit the part and rebuild:

- `parts/word/styles.xml` — heading, body and table styles
- `parts/word/theme/theme1.xml` — theme fonts and the accent palette
- `parts/word/header1.xml`, `parts/word/footer1.xml` — the banners
- `parts/word/document.xml` — the sample body and the landscape `sectPr`

## Using it

```bash
make loud-template            # build it
make restyle-e2e ENV=test     # build it, then prove a restyle end-to-end
```

`restyle-e2e` byte-compares the template's theme, header and footer against the
restyled document. It asserts on the bytes rather than on the converter's
`reference_doc_applied` flag, because "applied, carried 0 parts" is a legitimate
result for a template with nothing to give — trusting the flag alone would let a
real regression through.
