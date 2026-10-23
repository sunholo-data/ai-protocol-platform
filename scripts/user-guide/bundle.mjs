#!/usr/bin/env node
/**
 * Bundle the user guide for both images.
 *
 *   node scripts/user-guide/bundle.mjs [--check]
 *
 * Merges docs/user-guide/*.md (the platform pages) with any fork overlay in
 * docs/customers/<c>/user-guide/ and writes the result where each image's build
 * context can see it — the frontend and backend each build from their own
 * folder, so neither can read docs/ at build time:
 *
 *   frontend/content/user-guide/{manifest.json,<slug>.md}   pages for /help
 *   frontend/public/help/img/...                            screenshots
 *   backend/bundled/user-guide/{manifest.json,<slug>.md}    the platform-guide KB
 *
 * All three are build outputs (gitignored). Run from npm predev/prebuild and from
 * a Cloud Build step before the image builds. --check validates without writing.
 *
 * Overlay files: a page (new slug, or the same slug to replace the platform page),
 * `extends: <slug>` + `after: <anchor>` to append to the end of a section, and
 * `guide.yaml` with `tags: [...]` — who may use the guide on this deployment.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PLATFORM_DIR = path.join(ROOT, "docs/user-guide");
const CUSTOMERS_DIR = path.join(ROOT, "docs/customers");
const OUT = {
  frontend: path.join(ROOT, "frontend/content/user-guide"),
  images: path.join(ROOT, "frontend/public/help/img"),
  backend: path.join(ROOT, "backend/bundled/user-guide"),
};
const SKIP = new Set(["README.md"]);

export class GuideError extends Error {}

/** GitHub-style heading anchor. frontend/src/lib/userGuide.ts has the same function (keep in step; both are tested). */
export function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[*_`]/g, "")
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s/g, "-");
}

/** The front-matter subset the page contract uses: `key: value`, `key: [a, b]`, `key:` + `  - item` lines. */
export function parseFrontMatter(src, where = "page") {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(src);
  if (!m) return { meta: {}, body: src };
  const meta = {};
  let listKey = null;
  for (const raw of m[1].split("\n")) {
    const line = raw.replace(/(^|\s+)#.*$/, "");
    if (!line.trim()) continue;
    const item = /^\s+-\s+(.*)$/.exec(line);
    if (item && listKey) {
      meta[listKey].push(unquote(item[1]));
      continue;
    }
    const kv = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line);
    if (!kv) throw new GuideError(`${where}: can't read front matter line ${JSON.stringify(raw)}`);
    const [, key, value] = kv;
    if (value === "") {
      meta[key] = [];
      listKey = key;
    } else {
      listKey = null;
      const arr = /^\[(.*)\]$/.exec(value);
      meta[key] = arr ? arr[1].split(",").map((s) => unquote(s.trim())).filter(Boolean) : unquote(value);
    }
  }
  return { meta, body: src.slice(m[0].length) };
}

function unquote(s) {
  const q = /^(["'])(.*)\1$/.exec(s.trim());
  return q ? q[2] : s.trim();
}

/** Headings outside fenced code, with their anchors (duplicates get -1, -2 like GitHub). */
export function sections(body) {
  const out = [];
  const seen = new Map();
  let fenced = false;
  body.split("\n").forEach((line, index) => {
    if (/^```/.test(line)) fenced = !fenced;
    const h = !fenced && /^(#{2,3})\s+(.+?)\s*#*$/.exec(line);
    if (!h) return;
    const base = slugify(h[2]);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    out.push({ level: h[1].length, title: h[2], anchor: n ? `${base}-${n}` : base, line: index });
  });
  return out;
}

/** Add `extra` to the section whose anchor is `after`: at the end of its own text
 * (before any subsection), and before its trailing screenshots, so a section that
 * ends in images keeps its steps-beside-screenshot layout. */
export function insertAfter(body, after, extra, where) {
  const heads = sections(body);
  const at = heads.findIndex((s) => s.anchor === after);
  if (at < 0) {
    throw new GuideError(`${where}: no section #${after} to extend (have: ${heads.map((s) => s.anchor).join(", ")})`);
  }
  const next = heads[at + 1];
  const lines = body.split("\n");
  let cut = next ? next.line : lines.length;
  while (cut - 1 > heads[at].line && /^\s*$|^!\[/.test(lines[cut - 1])) cut -= 1;
  const before = lines.slice(0, cut).join("\n").replace(/\s+$/, "");
  const afterText = lines.slice(cut).join("\n");
  return `${before}\n\n${extra.trim()}\n\n${afterText}`.replace(/\n{3,}/g, "\n\n");
}

function readPages(dir, layer) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md") && !SKIP.has(f))
    .sort()
    .map((f) => {
      const file = path.join(dir, f);
      const { meta, body } = parseFrontMatter(fs.readFileSync(file, "utf8"), path.relative(ROOT, file));
      return { slug: f.replace(/\.md$/, ""), meta, body, layer, file, dir };
    });
}

function readTags(dir) {
  const f = path.join(dir, "guide.yaml");
  if (!fs.existsSync(f)) return [];
  const { meta } = parseFrontMatter(`---\n${fs.readFileSync(f, "utf8").trim()}\n---\n`, path.relative(ROOT, f));
  return Array.isArray(meta.tags) ? meta.tags : [];
}

/** Build the merged guide in memory: { manifest, pages: Map<slug, body>, images: [[src, rel]] }. */
export function build({ platformDir = PLATFORM_DIR, customersDir = CUSTOMERS_DIR } = {}) {
  const pages = new Map();
  for (const p of readPages(platformDir, "platform")) pages.set(p.slug, p);
  const overlays = fs.existsSync(customersDir)
    ? fs
        .readdirSync(customersDir)
        .sort()
        .map((c) => path.join(customersDir, c, "user-guide"))
        .filter((d) => fs.existsSync(d))
    : [];
  const tags = new Set();
  const extensions = [];
  for (const dir of overlays) {
    readTags(dir).forEach((t) => tags.add(t));
    for (const p of readPages(dir, "customer")) {
      if (p.meta.extends) extensions.push(p);
      else pages.set(p.slug, p);
    }
  }
  for (const ext of extensions) {
    const target = pages.get(ext.meta.extends);
    const where = path.relative(ROOT, ext.file);
    if (!target) throw new GuideError(`${where}: extends unknown page ${JSON.stringify(ext.meta.extends)}`);
    if (!ext.meta.after) throw new GuideError(`${where}: extends needs an 'after: <anchor>'`);
    target.body = insertAfter(target.body, ext.meta.after, ext.body, where);
    target.extendedBy = [...(target.extendedBy ?? []), path.relative(ROOT, ext.file)];
  }
  const images = [];
  const list = [...pages.values()].map((p) => {
    const where = path.relative(ROOT, p.file);
    if (!p.meta.title) throw new GuideError(`${where}: front matter needs a title`);
    for (const m of p.body.matchAll(/!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
      const rel = m[1];
      if (/^[a-z]+:/i.test(rel) || rel.startsWith("/")) continue;
      const src = path.join(p.dir, rel);
      if (!fs.existsSync(src)) throw new GuideError(`${where}: image ${rel} does not exist`);
      images.push([src, rel]);
    }
    return {
      slug: p.slug,
      title: p.meta.title,
      summary: p.meta.summary ?? "",
      audience: p.meta.audience ?? "user",
      order: Number(p.meta.order ?? 100),
      requires: p.meta.requires ?? [],
      surfaces: p.meta.surfaces ?? [],
      reviewed: p.meta.reviewed ?? "",
      layer: p.layer,
      extendedBy: p.extendedBy ?? [],
      sections: sections(p.body).map(({ level, title, anchor }) => ({ level, title, anchor })),
      file: `${p.slug}.md`,
    };
  });
  list.sort((a, b) => a.order - b.order || a.slug.localeCompare(b.slug));
  return {
    manifest: { tags: [...tags], pages: list },
    pages: new Map([...pages].map(([slug, p]) => [slug, p.body])),
    images,
  };
}

function writeTree(dir, guide) {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(guide.manifest, null, 2) + "\n");
  for (const [slug, body] of guide.pages) fs.writeFileSync(path.join(dir, `${slug}.md`), body);
}

export function write(guide, out = OUT) {
  writeTree(out.frontend, guide);
  writeTree(out.backend, guide);
  fs.rmSync(out.images, { recursive: true, force: true });
  for (const [src, rel] of guide.images) {
    const dest = path.join(out.images, rel.replace(/^img\//, ""));
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
}

function main() {
  if (!fs.existsSync(PLATFORM_DIR)) {
    // Inside an image build context there is no docs/ — the bundle was made before.
    console.log("user-guide: no docs/user-guide here; using the existing bundle");
    return;
  }
  const guide = build();
  const n = guide.manifest.pages.length;
  if (process.argv.includes("--check")) {
    console.log(`user-guide: ${n} page(s) OK`);
    return;
  }
  write(guide);
  const tags = guide.manifest.tags.length ? guide.manifest.tags.join(", ") : "none (set tags in docs/customers/<c>/user-guide/guide.yaml)";
  console.log(`user-guide: bundled ${n} page(s), ${guide.images.length} image(s); tags: ${tags}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (err) {
    console.error(`user-guide: ${err.message}`);
    process.exit(1);
  }
}
