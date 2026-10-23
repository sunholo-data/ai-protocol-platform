/**
 * Server-side loader for the bundled user guide (frontend/content/user-guide,
 * written by scripts/user-guide/bundle.mjs from docs/user-guide + fork overlays).
 * Read at build time by the static /help pages; nothing here runs in the browser.
 */

import fs from "node:fs";
import path from "node:path";

import type { GuideManifest, GuidePageMeta } from "./userGuideParse";

const DIR = path.join(process.cwd(), "content", "user-guide");

const EMPTY: GuideManifest = { tags: [], pages: [] };

export function readManifest(dir: string = DIR): GuideManifest {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8")) as GuideManifest;
  } catch {
    // No bundle (e.g. a checkout that never ran the bundler): /help says so
    // rather than 500ing. `npm run dev` / `build` run the bundler first.
    return EMPTY;
  }
}

export function readPage(slug: string, dir: string = DIR): { meta: GuidePageMeta; body: string } | null {
  const meta = readManifest(dir).pages.find((p) => p.slug === slug);
  if (!meta || !/^[\w-]+$/.test(slug)) return null;
  try {
    return { meta, body: fs.readFileSync(path.join(dir, `${slug}.md`), "utf8") };
  } catch {
    return null;
  }
}
