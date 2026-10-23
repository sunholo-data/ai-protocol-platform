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
