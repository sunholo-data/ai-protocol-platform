// node --test scripts/user-guide/*.test.mjs   (run by `make guide-check`)
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { boldLabels, checkPages, labelFragments } from "./check.mjs";

const CORPUS = "Make searchable · Add to a skill… · Searchable · Make my copy and add ‘x’ · Stop searching";
const TODAY = new Date("2026-10-16");

function tree(page, extra = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "guide-check-"));
  const files = { "docs/user-guide/folders.md": page, "docs/user-guide/img/a.png": "png", ...extra };
  for (const [rel, body] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), body);
  }
  return { platformDir: path.join(root, "docs/user-guide"), customersDir: path.join(root, "docs/customers"), corpus: CORPUS, today: TODAY };
}

const OK = `---
title: Folders
summary: About folders.
reviewed: 2026-10-01
---
## Make it searchable

1. Click **Make searchable**, then **Add to a skill…**
2. Badge reads **Searchable · 2**. Or **Make my copy and add ‘Phoenix’**.

> [!WARNING]
> **Your copy is a snapshot.**

![Menu](img/a.png "The menu.")
`;

test("a good page passes", () => {
  assert.deepEqual(checkPages(tree(OK)).errors, []);
});

test("labels: emphasis and code are skipped; placeholders and counts don't need to match", () => {
  assert.deepEqual(boldLabels("**A** and **an emphasis.** and `**not this**`"), ["A"]);
  assert.deepEqual(labelFragments("Make my copy and add ‘Phoenix’"), ["Make my copy and add"]);
  assert.deepEqual(labelFragments("Indexing 1/2"), ["Indexing"]);
});

test("each seeded defect is an error", () => {
  const cases = [
    [OK.replace("**Make searchable**", "**Make it findable**"), /\*\*Make it findable\*\* isn't in frontend\/src/],
    [OK.replace("![Menu]", "![]"), /has no alt text/],
    [OK.replace(' "The menu."', ""), /has no caption/],
    [OK.replace("img/a.png", "img/gone.png"), /image img\/gone.png does not exist/],
    [OK.replace("summary: About folders.\n", ""), /needs a summary/],
    [OK.replace("title: Folders\n", "title: Folders\naudience: everyone\n"), /audience "everyone"/],
  ];
  for (const [page, re] of cases) {
    const { errors } = checkPages(tree(page));
    assert.ok(errors.some((e) => re.test(e)), `expected ${re} in ${JSON.stringify(errors)}`);
  }
});

test("a platform page naming a customer fails; the same name in an overlay is fine", () => {
  const overlay = {
    "docs/customers/acme/user-guide/guide.yaml": "tags: [acme]\nnames: [Acme Assistant, acme.com]\n",
    "docs/customers/acme/user-guide/note.md": "---\nextends: folders\nafter: make-it-searchable\n---\nUse Acme Assistant.\n",
  };
  assert.deepEqual(checkPages(tree(OK, overlay)).errors, []);
  const leaky = OK.replace("## Make it searchable", "## Make it searchable\n\nAsk Acme Assistant.");
  assert.match(checkPages(tree(leaky, overlay)).errors.join("\n"), /names the customer "Acme Assistant"/);
});

test("stale or missing review dates warn, they don't fail", () => {
  const old = checkPages(tree(OK.replace("2026-10-01", "2025-01-01")));
  assert.deepEqual(old.errors, []);
  assert.match(old.warnings.join("\n"), /over 6 months ago/);
});

test("catalogMessages: labels that moved into interface catalogs still count", async () => {
  const { catalogMessages } = await import("./check.mjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "guide-catalog-"));
  fs.mkdirSync(path.join(dir, "en"), { recursive: true });
  fs.writeFileSync(path.join(dir, "en/settings.json"), JSON.stringify({ McpTokensSection: { create: "Create token", n: 3 } }));
  fs.writeFileSync(path.join(dir, "en/broken.json"), "{nope");
  assert.deepEqual(catalogMessages([dir]), ["Create token"]);
});

test("anchor links must point at a heading the page has", () => {
  const linked = OK.replace("1. Click", "See [below](#make-it-searchable) and [x](#gone). 1. Click");
  const { errors } = checkPages(tree(linked));
  assert.deepEqual(errors, ["folders.md: links to #gone, which folders.md doesn't have — a renamed heading?"]);
  const cross = OK.replace("1. Click", "[a](other.md#b) [c](folders.md#nope) [d](missing.md#x) 1. Click");
  const out = checkPages(tree(cross, { "docs/user-guide/other.md": "---\ntitle: O\nsummary: O.\nreviewed: 2026-10-01\n---\n## B\n" })).errors;
  assert.deepEqual(out, [
    "folders.md: links to #nope, which folders.md doesn't have — a renamed heading?",
    "folders.md: links to missing.md, which isn't a page",
  ]);
});

// Translations, with the made-up fixture locale `xx` and its own catalog strings.
const XX = `---
title: Xx folders
summary: Xx about folders.
reviewed: 2026-10-01
---
## Xx searchable

1. Xx **Xx gør søgbar**, xx **Stop searching**. See [here](#xx-searchable).

![Xx menu](img/a.png "Xx menu.")
`;

test("a translated page's labels are checked against its own locale's catalog plus the source corpus", () => {
  const xxCorpus = (locale) => (locale === "xx" ? "Xx gør søgbar" : "");
  const good = { ...tree(OK, { "docs/user-guide/xx/folders.md": XX }), localeCorpus: xxCorpus };
  const result = checkPages(good);
  assert.deepEqual(result.errors, []);
  assert.equal(result.translations, 1);

  const wrong = { ...tree(OK, { "docs/user-guide/xx/folders.md": XX.replace("**Xx gør søgbar**", "**Xx gør findbar**") }), localeCorpus: xxCorpus };
  assert.match(checkPages(wrong).errors.join("\n"), /xx\/folders\.md: \*\*Xx gør findbar\*\* isn't in the xx catalog/);

  // Another locale's catalog doesn't count, and a source page can't use the xx strings.
  const elsewhere = { ...tree(OK.replace("**Make searchable**", "**Xx gør søgbar**"), { "docs/user-guide/xx/folders.md": XX }), localeCorpus: xxCorpus };
  assert.deepEqual(checkPages(elsewhere).errors, ["folders.md: **Xx gør søgbar** isn't in frontend/src (missing: \"Xx gør søgbar\") — renamed?"]);
});

test("a translation's anchors are its own; links to another page use that page's translation when there is one", () => {
  const xxCorpus = () => "Xx gør søgbar";
  const sourceAnchor = XX.replace("[here](#xx-searchable)", "[here](#make-it-searchable)");
  assert.match(checkPages({ ...tree(OK, { "docs/user-guide/xx/folders.md": sourceAnchor }), localeCorpus: xxCorpus }).errors.join("\n"), /xx\/folders\.md: links to #make-it-searchable/);
  const other = "---\ntitle: O\nsummary: O.\nreviewed: 2026-10-01\n---\n## B\n";
  const toOther = XX.replace("[here](#xx-searchable)", "[o](other.md#b)");
  // No xx translation of other.md: the reader sees the source page, so its anchor is right.
  assert.deepEqual(checkPages({ ...tree(OK, { "docs/user-guide/other.md": other, "docs/user-guide/xx/folders.md": toOther }), localeCorpus: xxCorpus }).errors, []);
  // With one, the reader sees the translation, whose heading differs.
  const withXx = { "docs/user-guide/other.md": other, "docs/user-guide/xx/other.md": other.replace("## B", "## Xx b"), "docs/user-guide/xx/folders.md": toOther };
  assert.match(checkPages({ ...tree(OK, withXx), localeCorpus: xxCorpus }).errors.join("\n"), /xx\/folders\.md: links to #b, which other\.md doesn't have/);
});

test("localeCorpus reads only that locale's catalogs, template and fork", async () => {
  const { localeCorpus, messageDirs } = await import("./check.mjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "guide-locale-"));
  const put = (rel, data) => {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), JSON.stringify(data));
  };
  put("messages/xx/a.json", { A: { k: "Xx template" } });
  put("messages/yy/a.json", { A: { k: "Yy template" } });
  put("customers/acme/messages/xx/a.json", { A: { k: "Xx fork" } });
  const opts = { messagesDir: path.join(root, "messages"), customersDir: path.join(root, "customers") };
  assert.equal(messageDirs("xx", opts).length, 2);
  const text = localeCorpus("xx", opts);
  assert.ok(text.includes("Xx template") && text.includes("Xx fork") && !text.includes("Yy"));
});
