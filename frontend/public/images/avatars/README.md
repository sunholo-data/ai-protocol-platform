# Default skill avatars

The Skill Studio ("Create a new skill") offers the icons in `gallery/` — one
**cohesive** set, listed in `src/lib/defaultAvatars.ts`. A new skill starts
with a random one. Consistency is the whole point: a matched set looks
intentional; a grab-bag looks cheap.

## Style spec — make new icons to match

- **Subject:** one small 3D object that says what KIND of skill it is (a chat
  bubble, a magnifying glass over a globe, a funnel of cubes…) — no people,
  no text, no letters, no logos.
- **Look:** Blender-style matte clay, soft studio light, isometric
  three-quarter view, sitting on a rounded slate-blue square plinth.
- **Colours:** slate blue, coral orange, teal and off-white only.
- **Background:** pure white `#FFFFFF` edge to edge — no backdrop, no gradient,
  no cast shadow (it sits in a round avatar).
- **File:** square, trimmed to the object with ~12% margin, 256×256 WebP
  (`quality 88`), ~5 KB each. Name it by kind: `gallery/<id>.webp`.

The reference renders are the Sunholo website's service icons
(sunholo.com/assets). The set here was generated on 2026-10-01 with Gemini 2.5
Flash Image (Vertex AI), passing three of those renders as style references
plus the spec above as the prompt; reject any result whose corners are not
pure white and regenerate.

## Adding one

1. Generate the image to the spec, trim/pad/resize to 256×256 WebP.
2. Drop it in `gallery/`.
3. Add `gallery("<id>", "<Label>")` to `DEFAULT_AVATARS` in
   `frontend/src/lib/defaultAvatars.ts`.

## The older files in this folder

`ana.jpg`, `marco.jpg` and the `skill-*.svg` glyphs were the previous set.
They are no longer offered in the Studio but are kept on purpose: template
`SKILL.md` frontmatter and skills already stored in any deployment's database
point at these paths, and removing a file would break those avatars.
