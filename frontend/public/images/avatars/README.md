# Default skill avatars

A small, **stylistically cohesive** set of professional avatars users pick from
in the Skill Studio. Consistency is the whole point — a matched set looks
intentional; a grab-bag looks cheap. (The v5 avatar library was a grab-bag of
clashing styles + trademarked characters, so it was deliberately not reused.)

## Style spec — generate new avatars to match

- **Form:** head-and-shoulders portrait, centred, facing forward.
- **Look:** flat vector illustration — clean bold outlines, simple flat shading,
  friendly and approachable, professional/business-casual attire.
- **Background:** plain light / very-soft-gradient, no scenery, no props, no text.
- **Framing:** square 1:1, subject centred so it crops cleanly to a circle.
- **Size / format:** export 512×512, optimised JPG (or PNG/SVG if flat enough),
  aim for < 60 KB. Resize with `sips -s format jpeg -Z 512 in.png --out out.jpg`.
- **Diversity:** vary hair, skin tone, gender, glasses, attire colour — but keep
  the SAME line weight, shading, and background treatment across the whole set.

The seed avatars `ana.jpg` and `marco.jpg` are the reference. Match their line
weight and flat-shading style.

## Adding one

1. Generate/resize the image to the spec above.
2. Drop it in this folder.
3. Add an entry to `frontend/src/lib/defaultAvatars.ts`:
   ```ts
   { id: "kebab-id", label: "Friendly Name", src: "/images/avatars/file.jpg" },
   ```
