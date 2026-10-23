# Brand: the template's look, and how a fork restyles it

The template ships styled in the **Sunholo brand** (sunholo.com/assets, kit
v1): white paper, ink `#2C3E50` text, slate structure, Action orange `#BD3012`
for primary actions and links, Montserrat, 3px corners, flat surfaces with 1px
rules, a derived slate-night dark mode, and a matching code-highlight palette.
All of it lives as CSS variables in `src/app/globals.css`.

To restyle a fork, **add a file — don't edit the template.** Set
`NEXT_PUBLIC_BRAND_THEME_CSS` to a stylesheet under this folder (for example
`/brand/<your-fork>/theme.css`); it is linked after the bundled CSS. Override
only what differs:

```css
html:root {
  --primary: 221 83% 53%;           /* your action colour (HSL triplet) */
  --ring: 221 83% 53%;
  --radius: 0.5rem;                 /* softer corners */
  --brand-accent: #2563eb;          /* mark/emphasis colour */
  --brand-slate: #1e293b;           /* wordmark colour */
  --code-keyword: #2563eb;          /* code palette: --code-* */
}
html body { font-family: "Inter", system-ui, sans-serif; }   /* another face */
html .shadow-md { box-shadow: 0 4px 12px rgb(0 0 0 / 0.08); } /* want depth? */
```

Use `html:root` / `html.dark` selectors: the file is linked rather than
bundled, so specificity (not load order) is what makes it win.

Everything else brand-related (name, tagline, logos, landing copy) is
env-driven — see `src/lib/branding.ts`.

## Already edited `globals.css` or `tailwind.config.ts`?

Forks styled before 2026-10-01 (Aitana, AIPLA) edited those template files
directly. On the merge that brings in the Sunholo default you will get
conflicts there: take upstream's version and move your values into a theme file
as above. Step by step: `UPGRADING.md` at the repo root.

## Home-screen app (PWA)

The app is installable ("Add to Home Screen"). To brand the installed app:

- `NEXT_PUBLIC_BRAND_THEME_COLOR` — status-bar / splash colour; match your
  theme's `--background` (default `#ffffff`).
- Icons are PNGs in `public/images/logo/` (`apple-touch-icon.png`,
  `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`). Regenerate them from
  your own mark with `make pwa-icons SRC=frontend/public/images/logo/<mark>.svg BG='#rrggbb'`.
- `NEXT_PUBLIC_SERVICE_WORKER=0` turns the service worker off (and removes it
  from browsers that already have it).

## Skill art

`NEXT_PUBLIC_BRAND_SKILL_ART_DIR` (e.g. `/brand/<your-fork>/skills/art`) gives
skills an illustration behind their fresh-chat landing: the file
`<dir>/<skill-slug>.webp` is shown faded behind the welcome and composer, and
fades out once the conversation starts. Optional per skill — a skill without a
file shows no art. Landscape (3:2) artwork on white works best.
