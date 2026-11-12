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
 *   frontend/content/user-guide/<locale>/{manifest.json,<slug>.md}   translations
 *   frontend/public/help/img/...                            screenshots
 *   backend/bundled/user-guide/...                          the same, for the platform-guide KB
 *
 * All three are build outputs (gitignored). Run from npm predev/prebuild and from
 * a Cloud Build step before the image builds. --check validates without writing.
 *
 * Overlay files: a page (new slug, or the same slug to replace the platform page),
 * `extends: <slug>` + `after: <anchor>` to append to the end of a section, and
 * `guide.yaml` with `tags: [...]` — who may use the guide on this deployment.
 *
 * Translations (multi-language.md §5): `<locale>/<slug>.md` beside the source
 * pages, in the template or an overlay. The same overlay rules apply within each
 * locale. A translation's slug must be a source page; it inherits `audience`,
 * `order` and `requires` from it when it omits them, and uses the same shared
 * `img/…` paths. The top manifest keeps its shape for the source pages and adds
 * `locales: {<locale>: [slugs]}`; each locale gets its own manifest beside its pages.
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
/** The source pages' language (frontend/src/i18n/locale.ts SOURCE_LOCALE). */
export const SOURCE_LOCALE = "en";
/** A translation folder: a BCP 47 language[-region] tag, as normalised by the frontend (`pt-BR`). */
const LOCALE_TAG = /^[a-z]{2,3}(?:-(?:[A-Z]{2}|\d{3}))?$/;
/** Folders beside the pages that are never a locale. */
const NOT_LOCALES = new Set(["img"]);

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

/** `assetDir` is where the page's `img/…` paths resolve: its own folder, or the guide root for a translation. */
function readPages(dir, layer, assetDir = dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md") && !SKIP.has(f))
    .sort()
    .map((f) => {
      const file = path.join(dir, f);
      const { meta, body } = parseFrontMatter(fs.readFileSync(file, "utf8"), path.relative(ROOT, file));
      return { slug: f.replace(/\.md$/, ""), meta, body, layer, file, dir: assetDir };
    });
}

/** The translation folders in a guide folder: `{locale, dir}` for each `<locale>/`. */
function localeDirs(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    // Other folders (drafts/, notes/) are not pages, as before translations existed;
    // one that is nearly a tag (DA/, pt_br/) is a mistake worth stopping on.
    .filter((e) => e.isDirectory() && !NOT_LOCALES.has(e.name) && /^[a-z]{2,3}(?:[-_][a-z\d]{2,3})?$/i.test(e.name))
    .map((e) => {
      const where = path.relative(ROOT, path.join(dir, e.name));
      if (!LOCALE_TAG.test(e.name)) throw new GuideError(`${where}/: write the locale as a tag like da, de or pt-BR`);
      if (e.name === SOURCE_LOCALE) {
        throw new GuideError(`${where}/: the source pages are ${SOURCE_LOCALE} — put them beside it, not in ${e.name}/`);
      }
      return { locale: e.name, dir: path.join(dir, e.name) };
    })
    .sort((a, b) => a.locale.localeCompare(b.locale));
}

/** Pages plus overlay pages (replace by slug) and `extends:` appends: the merge rule, for one locale. */
function mergeLayers(layers) {
  const pages = new Map();
  const extensions = [];
  for (const layer of layers) {
    for (const p of layer) {
      if (p.layer === "customer" && p.meta.extends) extensions.push(p);
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
  return pages;
}

/** A page's manifest entry; its images are pushed onto `images`. `inherit` is the source page's meta (+ `imageDir`), for a translation. */
function pageMeta(p, images, inherit = {}) {
  const where = path.relative(ROOT, p.file);
  if (!p.meta.title) throw new GuideError(`${where}: front matter needs a title`);
  for (const m of p.body.matchAll(/!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
    const rel = m[1];
    if (/^[a-z]+:/i.test(rel) || rel.startsWith("/")) continue;
    // A translation shares its source page's screenshots: its own guide folder, then the source page's.
    const src = [p.dir, ...(inherit.imageDir ? [inherit.imageDir] : [])].map((d) => path.join(d, rel)).find((f) => fs.existsSync(f));
    if (!src) throw new GuideError(`${where}: image ${rel} does not exist`);
    images.push([src, rel]);
  }
  return {
    slug: p.slug,
    title: p.meta.title,
    summary: p.meta.summary ?? "",
    audience: p.meta.audience ?? inherit.audience ?? "user",
    order: Number(p.meta.order ?? inherit.order ?? 100),
    requires: p.meta.requires ?? inherit.requires ?? [],
    surfaces: p.meta.surfaces ?? [],
    reviewed: p.meta.reviewed ?? "",
    layer: p.layer,
    extendedBy: p.extendedBy ?? [],
    sections: sections(p.body).map(({ level, title, anchor }) => ({ level, title, anchor })),
    file: `${p.slug}.md`,
  };
}

const byOrder = (a, b) => a.order - b.order || a.slug.localeCompare(b.slug);

function readTags(dir) {
  const f = path.join(dir, "guide.yaml");
  if (!fs.existsSync(f)) return [];
  const { meta } = parseFrontMatter(`---\n${fs.readFileSync(f, "utf8").trim()}\n---\n`, path.relative(ROOT, f));
  return Array.isArray(meta.tags) ? meta.tags : [];
}

/**
 * Build the merged guide in memory: { manifest, pages: Map<slug, body>, images: [[src, rel]],
 * translations: Map<locale, { manifest, pages: Map<slug, body> }> }.
 */
export function build({ platformDir = PLATFORM_DIR, customersDir = CUSTOMERS_DIR } = {}) {
  const overlays = fs.existsSync(customersDir)
    ? fs
        .readdirSync(customersDir)
        .sort()
        .map((c) => path.join(customersDir, c, "user-guide"))
        .filter((d) => fs.existsSync(d))
    : [];
  const tags = new Set();
  for (const dir of overlays) readTags(dir).forEach((t) => tags.add(t));
  const pages = mergeLayers([readPages(platformDir, "platform"), ...overlays.map((d) => readPages(d, "customer"))]);

  const images = [];
  const list = [...pages.values()].map((p) => pageMeta(p, images));
  list.sort(byOrder);
  const source = new Map(list.map((m) => [m.slug, m]));

  // Each locale merges like the source: template translations, then overlays.
  const layersByLocale = new Map();
  for (const [dir, layer] of [[platformDir, "platform"], ...overlays.map((d) => [d, "customer"])]) {
    for (const { locale, dir: ldir } of localeDirs(dir)) {
      if (!layersByLocale.has(locale)) layersByLocale.set(locale, []);
      layersByLocale.get(locale).push(readPages(ldir, layer, dir));
    }
  }
  const translations = new Map();
  for (const locale of [...layersByLocale.keys()].sort()) {
    const merged = mergeLayers(layersByLocale.get(locale));
    const metas = [...merged.values()].map((p) => {
      const of = source.get(p.slug);
      if (!of) {
        throw new GuideError(
          `${path.relative(ROOT, p.file)}: translates unknown page ${JSON.stringify(p.slug)} — a translation needs a source page with the same slug`,
        );
      }
      return pageMeta(p, images, { ...of, imageDir: pages.get(p.slug).dir });
    });
    if (!metas.length) continue;
    metas.sort(byOrder);
    translations.set(locale, {
      manifest: { locale, pages: metas },
      pages: new Map([...merged].map(([slug, p]) => [slug, p.body])),
    });
  }

  return {
    manifest: {
      tags: [...tags],
      pages: list,
      locales: Object.fromEntries([...translations].map(([l, t]) => [l, t.manifest.pages.map((p) => p.slug)])),
    },
    pages: new Map([...pages].map(([slug, p]) => [slug, p.body])),
    images,
    translations,
  };
}

function writeTree(dir, guide) {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(guide.manifest, null, 2) + "\n");
  for (const [slug, body] of guide.pages) fs.writeFileSync(path.join(dir, `${slug}.md`), body);
  for (const [locale, t] of guide.translations ?? []) {
    const ldir = path.join(dir, locale);
    fs.mkdirSync(ldir, { recursive: true });
    fs.writeFileSync(path.join(ldir, "manifest.json"), JSON.stringify(t.manifest, null, 2) + "\n");
    for (const [slug, body] of t.pages) fs.writeFileSync(path.join(ldir, `${slug}.md`), body);
  }
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
  const tr = Object.entries(guide.manifest.locales).map(([l, slugs]) => `${l}: ${slugs.length}`);
  const translated = tr.length ? `; translations ${tr.join(", ")}` : "";
  if (process.argv.includes("--check")) {
    console.log(`user-guide: ${n} page(s) OK${translated}`);
    return;
  }
  write(guide);
  const tags = guide.manifest.tags.length ? guide.manifest.tags.join(", ") : "none (set tags in docs/customers/<c>/user-guide/guide.yaml)";
  console.log(`user-guide: bundled ${n} page(s), ${guide.images.length} image(s)${translated}; tags: ${tags}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (err) {
    console.error(`user-guide: ${err.message}`);
    process.exit(1);
  }
}
