---
name: ui-guidelines-check
description: Check changed frontend code against Vercel's web interface guidelines (accessibility, focus, forms, navigation and URL state, copy) and this repo's UI rules, reporting file:line findings. Use before opening or updating a pull request that changes frontend/src, or when asked to review UI code, check accessibility or audit a component. Scoped to the diff; not a whole-repo sweep.
---

# UI guidelines check

Checks the UI code a change touches. It catches what a visual review misses:
mouse-only controls, focus that disappears, errors no one hears, Back that
leaves the app. It says nothing about layout or how busy a screen is; review
that separately.

## Steps

1. **Scope.** List changed UI files:
   `git diff --name-only origin/dev...HEAD -- 'frontend/src/**/*.tsx' ':!**/__tests__/**'`
   (or the files the user names). Review the changed hunks and the component
   they sit in, not the whole repo.
2. **Rules.** Fetch the current guidelines:
   `https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`
   Then read `frontend/CLAUDE.md` for this repo's own rules and UI kit.
3. **Check** each file against both. Read the line before reporting it.
4. **Report** in the guideline's terse `file:line – issue → fix` format,
   grouped by file, real problems first. Fix what's in scope for the change.

## Skip these (they don't fit this app)

- Title Case for headings and buttons: the app uses sentence case.
- Dark-mode rules (`color-scheme`, `theme-color`): no dark theme is switched on.
- `width`/`height` on images sized by CSS, and `touch-action` everywhere.
- Icons already marked `aria-hidden`, and lucide icons inside a labelled button.

Expect roughly a quarter of raw findings to be noise like this; leave it out of
the report rather than listing it.
