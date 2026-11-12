# Frontend — UI rules

Read this before changing anything under `frontend/src`. The root CLAUDE.md
covers setup and validation (`npm run quality:check`).

## Use the kit

`src/components/ui/` has `Button` (variants, `pending`), `Spinner`,
`Skeleton`/`PageLoading`, `Badge`, `notify` (toasts) and `useConfirm()`.
Use them rather than one-off markup, so loading, success, failure and
confirmation look and behave the same everywhere.

## Simple by default

Show what most people use on every visit; put the rest one labelled click away
(a menu, a disclosure, a section further down). No more than two levels deep.
Write labels in the user's words, sentence case, without internal names.

## Five rules the guideline audit keeps finding

1. **No `<div onClick>`.** Actions are `<button>`, navigation is `<Link>`, so
   both work from the keyboard and a link can open in a new tab.
2. **Don't hide status behind `aria-label`.** A label replaces the visible text
   for screen readers, so "Copied", "Opening…" or an error vanishes. Use
   `aria-describedby` or visible text.
3. **Hover-only controls also show on focus and touch:** pair `opacity-0
   group-hover:opacity-100` with `focus-visible:opacity-100
   [@media(hover:none)]:opacity-100`.
4. **Confirm with `useConfirm()`,** never an inline "Are you sure?" row. The
   dialog manages focus and Escape.
5. **`router.push` for navigation the user starts** (opening a chat, New chat),
   so Back works. `router.replace` only for the app's own URL bookkeeping.

To check a UI change against Vercel's full web interface guidelines, use the
`ui-guidelines-check` skill (`.claude/skills/ui-guidelines-check`).
