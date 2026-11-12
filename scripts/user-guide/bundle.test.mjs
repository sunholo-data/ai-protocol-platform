// node --test scripts/user-guide/   (run by `make guide-check`)
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { GuideError, build, insertAfter, parseFrontMatter, sections, slugify, write } from "./bundle.mjs";

function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "guide-"));
  for (const [rel, body] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), body);
  }
  return { root, platformDir: path.join(root, "docs/user-guide"), customersDir: path.join(root, "docs/customers") };
}

const PAGE = `---
title: Folders
summary: About folders.
order: 2
requires: [knowledge_bases]
surfaces:
  - frontend/src/components/doc-browser/
---

Intro.

## Make a folder searchable

1. Click **Make searchable**.

![Menu](img/f/menu.png "Turning search on.")

### Indexing

Text.

## Stop searching

Done.
`;

test("slugify matches GitHub anchors (same cases as userGuide.test.ts)", () => {
  assert.equal(slugify("When the skill isn't yours to change"), "when-the-skill-isnt-yours-to-change");
  assert.equal(slugify("Upload a folder — from the CLI"), "upload-a-folder--from-the-cli");
  assert.equal(slugify("**Bold** `code` heading"), "bold-code-heading");
});

test("front matter: scalars, inline lists, block lists, comments", () => {
  const { meta, body } = parseFrontMatter(PAGE);
  assert.equal(meta.title, "Folders");
  assert.deepEqual(meta.requires, ["knowledge_bases"]);
  assert.deepEqual(meta.surfaces, ["frontend/src/components/doc-browser/"]);
  assert.match(body, /^\nIntro\./);
  assert.deepEqual(parseFrontMatter("---\n# c\ntags: [A, b] # who\n---\n").meta, { tags: ["A", "b"] });
});

test("sections ignore headings inside code fences and number duplicates", () => {
  const s = sections("## A\n```\n## not a heading\n```\n## A\n### B\n");
  assert.deepEqual(
    s.map((x) => [x.level, x.anchor]),
    [[2, "a"], [2, "a-1"], [3, "b"]],
  );
});

test("extends inserts before the section's trailing images and its subsections", () => {
  const { body } = parseFrontMatter(PAGE);
  const out = insertAfter(body, "make-a-folder-searchable", "> [!TIP]\n> Extra.", "t");
  const tip = out.indexOf("> [!TIP]");
  assert.ok(tip > out.indexOf("Click **Make searchable**"));
  assert.ok(tip < out.indexOf("![Menu]"), "screenshot stays last, so the layout holds");
  assert.ok(out.indexOf("![Menu]") < out.indexOf("### Indexing"));
});

test("an unknown anchor fails loudly, naming the anchors that exist", () => {
  assert.throws(() => insertAfter("## A\n", "nope", "x", "f.md"), (e) => e instanceof GuideError && /#nope.*have: a/.test(e.message));
});

test("build merges overlays: tags, extends, replace, layers; write mirrors both trees", () => {
  const t = tree({
    "docs/user-guide/folders.md": PAGE,
    "docs/user-guide/img/f/menu.png": "png",
    "docs/user-guide/README.md": "# not a page",
    "docs/customers/acme/user-guide/guide.yaml": "# who\ntags: [acme, platform-admin]\n",
    "docs/customers/acme/user-guide/note.md": "---\nextends: folders\nafter: make-a-folder-searchable\n---\n> [!TIP]\n> Acme note.\n",
    "docs/customers/acme/user-guide/acme-start.md": "---\ntitle: Getting started at Acme\norder: 1\n---\n## Hello\n",
  });
  const guide = build(t);
  assert.deepEqual(guide.manifest.tags, ["acme", "platform-admin"]);
  assert.deepEqual(guide.manifest.pages.map((p) => [p.slug, p.layer]), [["acme-start", "customer"], ["folders", "platform"]]);
  assert.match(guide.pages.get("folders"), /Acme note/);
  assert.ok(!guide.pages.has("note") && !guide.pages.has("README"));
  const folders = guide.manifest.pages.find((p) => p.slug === "folders");
  assert.deepEqual(folders.requires, ["knowledge_bases"]);
  assert.deepEqual(folders.sections.map((s) => s.anchor), ["make-a-folder-searchable", "indexing", "stop-searching"]);

  const out = { frontend: path.join(t.root, "fe"), backend: path.join(t.root, "be"), images: path.join(t.root, "img") };
  write(guide, out);
  assert.equal(fs.readFileSync(path.join(out.frontend, "folders.md"), "utf8"), fs.readFileSync(path.join(out.backend, "folders.md"), "utf8"));
  assert.ok(fs.existsSync(path.join(out.images, "f/menu.png")));
});

test("a missing image or title is an error, not a broken page", () => {
  assert.throws(() => build(tree({ "docs/user-guide/a.md": "---\ntitle: A\n---\n![x](img/missing.png)\n" })), /image img\/missing.png does not exist/);
  assert.throws(() => build(tree({ "docs/user-guide/a.md": "## no front matter\n" })), /needs a title/);
});

test("extending a page that doesn't exist fails", () => {
  const t = tree({
    "docs/user-guide/a.md": "---\ntitle: A\n---\n## X\n",
    "docs/customers/c/user-guide/e.md": "---\nextends: nope\nafter: x\n---\nhi\n",
  });
  assert.throws(() => build(t), /extends unknown page "nope"/);
});

// Translations (multi-language.md §5). `xx` is the made-up fixture locale; no real
// language is implied upstream.
const XX_PAGE = `---
title: Xx folders
summary: Xx about folders.
---
## Xx searchable

1. Xx **Make searchable**.

![Xx menu](img/f/menu.png "Xx caption.")
`;

test("a translation is emitted beside its source page, with manifest locales and inherited meta", () => {
  const t = tree({
    "docs/user-guide/folders.md": PAGE,
    "docs/user-guide/other.md": "---\ntitle: Other\norder: 9\n---\n## O\n",
    "docs/user-guide/img/f/menu.png": "png",
    "docs/user-guide/xx/folders.md": XX_PAGE,
  });
  const guide = build(t);
  // The source manifest keeps its shape; `locales` is added.
  assert.deepEqual(guide.manifest.pages.map((p) => p.slug), ["folders", "other"]);
  assert.deepEqual(guide.manifest.locales, { xx: ["folders"] });
  const xx = guide.translations.get("xx");
  const meta = xx.manifest.pages[0];
  assert.equal(xx.manifest.locale, "xx");
  assert.equal(meta.title, "Xx folders");
  assert.equal(meta.audience, "user");
  assert.equal(meta.order, 2, "order inherited from the source page");
  assert.deepEqual(meta.requires, ["knowledge_bases"], "requires inherited from the source page");
  assert.deepEqual(meta.sections.map((s) => s.anchor), ["xx-searchable"]);
  assert.match(xx.pages.get("folders"), /Xx \*\*Make searchable\*\*/);

  const out = { frontend: path.join(t.root, "fe"), backend: path.join(t.root, "be"), images: path.join(t.root, "img") };
  write(guide, out);
  for (const dir of [out.frontend, out.backend]) {
    assert.match(fs.readFileSync(path.join(dir, "xx/folders.md"), "utf8"), /Xx searchable/);
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, "xx/manifest.json"), "utf8")).pages[0].slug, "folders");
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8")).locales, { xx: ["folders"] });
  }
  assert.ok(fs.existsSync(path.join(out.images, "f/menu.png")), "translations share the source's images");
});

test("a translation's own audience/order win over the source page's", () => {
  const t = tree({
    "docs/user-guide/a.md": "---\ntitle: A\naudience: admin\norder: 5\n---\n",
    "docs/user-guide/xx/a.md": "---\ntitle: Xa\naudience: owner\norder: 7\nrequires: []\n---\n",
  });
  const [meta] = build(t).translations.get("xx").manifest.pages;
  assert.deepEqual([meta.audience, meta.order, meta.requires], ["owner", 7, []]);
});

test("no translations: locales is empty and nothing else changes", () => {
  const guide = build(tree({ "docs/user-guide/a.md": "---\ntitle: A\n---\n" }));
  assert.deepEqual(guide.manifest.locales, {});
  assert.equal(guide.translations.size, 0);
});

test("a translation of a page that doesn't exist is refused", () => {
  const t = tree({
    "docs/user-guide/a.md": "---\ntitle: A\n---\n",
    "docs/user-guide/xx/nope.md": "---\ntitle: Xx nope\n---\n",
  });
  assert.throws(() => build(t), (e) => e instanceof GuideError && /translates unknown page "nope"/.test(e.message));
});

test("a fork's own page can be translated, in the fork's folder", () => {
  const t = tree({
    "docs/user-guide/a.md": "---\ntitle: A\n---\n",
    "docs/customers/acme/user-guide/acme-start.md": "---\ntitle: Start\n---\n",
    "docs/customers/acme/user-guide/xx/acme-start.md": "---\ntitle: Xx start\n---\n",
    // A fork's translation of a TEMPLATE page uses the template's screenshots.
    "docs/user-guide/folders.md": PAGE,
    "docs/user-guide/img/f/menu.png": "png",
    "docs/customers/acme/user-guide/xx/folders.md": XX_PAGE,
  });
  const guide = build(t);
  assert.deepEqual(guide.manifest.locales, { xx: ["folders", "acme-start"] }, "in the source order");
  assert.ok(guide.translations.get("xx").manifest.pages.every((p) => p.layer === "customer"));
});

test("overlays apply per locale: replace the template translation, extend it", () => {
  const t = tree({
    "docs/user-guide/folders.md": PAGE,
    "docs/user-guide/img/f/menu.png": "png",
    "docs/user-guide/xx/folders.md": XX_PAGE,
    "docs/user-guide/xx/other.md": "---\ntitle: Xx other\n---\n## Xx o\n",
    "docs/user-guide/other.md": "---\ntitle: Other\n---\n## O\n",
    "docs/customers/acme/user-guide/xx/other.md": "---\ntitle: Acme xx other\n---\n## Acme xx\n",
    "docs/customers/acme/user-guide/xx/note.md": "---\nextends: folders\nafter: xx-searchable\n---\nAcme xx note.\n",
  });
  const guide = build(t);
  const xx = guide.translations.get("xx");
  assert.equal(xx.manifest.pages.find((p) => p.slug === "other").title, "Acme xx other");
  assert.equal(xx.manifest.pages.find((p) => p.slug === "other").layer, "customer");
  assert.match(xx.pages.get("folders"), /Acme xx note/);
  assert.ok(!xx.pages.has("note"));
  assert.doesNotMatch(guide.pages.get("folders"), /Acme xx note/, "the source page is untouched");
  assert.deepEqual(guide.manifest.locales.xx, ["folders", "other"]);
});

test("locale folders: a near-miss tag or the source locale is refused; img/ and other folders are not locales", () => {
  const base = { "docs/user-guide/a.md": "---\ntitle: A\n---\n", "docs/user-guide/img/x.png": "png" };
  assert.deepEqual(build(tree(base)).manifest.locales, {});
  assert.deepEqual(build(tree({ ...base, "docs/user-guide/drafts/a.md": "---\ntitle: A\n---\n" })).manifest.locales, {});
  assert.throws(() => build(tree({ ...base, "docs/user-guide/pt_br/a.md": "---\ntitle: A\n---\n" })), /tag like da, de or pt-BR/);
  assert.throws(() => build(tree({ ...base, "docs/user-guide/DA/a.md": "---\ntitle: A\n---\n" })), /tag like da, de or pt-BR/);
  assert.throws(() => build(tree({ ...base, "docs/user-guide/en/a.md": "---\ntitle: A\n---\n" })), /source pages are en/);
  assert.equal(build(tree({ ...base, "docs/user-guide/pt-BR/a.md": "---\ntitle: Pa\n---\n" })).manifest.locales["pt-BR"][0], "a");
});
