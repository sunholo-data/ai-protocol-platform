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
