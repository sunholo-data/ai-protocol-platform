#!/usr/bin/env node
// Regenerate the home-screen / PWA icon PNGs from one SVG source.
//
//   node scripts/generate-pwa-icons.mjs [source.svg] [background]
//   make pwa-icons SRC=public/images/logo/my-mark.svg BG='#ffffff'
//
// Output lands in public/images/logo/ — the same "swap the files" rebrand
// convention as the logos (see src/lib/branding.ts). A fork runs this once
// against its own mark and commits the PNGs.
//
// Every output is flattened onto an OPAQUE background. iOS requires an opaque
// square apple-touch-icon (it applies its own rounding); given transparency it
// composites onto black or falls back to a screenshot of the page (learned the
// hard way in the AIPLA fork, 2026-08-14). The maskable icon keeps the glyph
// inside the inner 80% safe zone because Android crops it to its own shape.
//
// The mark is trimmed to its visible shape and centred, then sized to about
// half the tile: brand guides typically ask for clear space of a quarter of
// the mark on every side (Sunholo's does), and an SVG's own viewBox is often
// off-centre around the artwork.
//
// Uses sharp, which Next.js already installs for image optimisation.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = process.argv[2] || join(root, "public/images/logo/sunholo-logo.svg");
const bg = process.argv[3] || "#ffffff";
const out = join(root, "public/images/logo");

let sharp;
try {
  sharp = (await import("sharp")).default;
} catch {
  console.error("FATAL: sharp not installed — run `npm install` in frontend/ first.");
  process.exit(1);
}

const svg = readFileSync(src);

// Rendered once, transparent, trimmed to the artwork's own bounds.
const trimmed = await sharp(svg, { density: 1024 }).png().trim().toBuffer();

async function icon(size, name, glyphFraction) {
  const glyph = Math.round(size * glyphFraction);
  const mark = await sharp(trimmed)
    .resize(glyph, glyph, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  await sharp({ create: { width: size, height: size, channels: 3, background: bg } })
    .composite([{ input: mark, gravity: "centre" }])
    .flatten({ background: bg })
    .removeAlpha()
    .png()
    .toFile(join(out, name));
  console.log(`  ${name}  ${size}x${size}`);
}

console.log(`PWA icons from ${src} on ${bg}:`);
await icon(180, "apple-touch-icon.png", 0.56); // iOS home screen — opaque, unrounded
await icon(192, "icon-192.png", 0.56); // manifest, purpose "any"
await icon(512, "icon-512.png", 0.56); // manifest, purpose "any"
await icon(512, "icon-maskable-512.png", 0.5); // manifest, purpose "maskable"
// iOS also probes /apple-touch-icon.png at the site root when it can't use the
// <link> (some share-sheet paths do), so the root copy is not redundant.
await sharp(join(out, "apple-touch-icon.png")).toFile(join(root, "public/apple-touch-icon.png"));
console.log("  ../../apple-touch-icon.png  (root copy)");
console.log("iOS caches home-screen icons: remove and re-add the shortcut to see a change.");
