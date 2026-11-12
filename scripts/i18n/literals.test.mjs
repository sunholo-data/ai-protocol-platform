import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { compare, groupOf, literalsIn, readBaseline, scan, scanned } from "./literals.mjs";

const CLI = path.join(import.meta.dirname, "literals.mjs");

/** A temp frontend: <root>/src/... from a { "src/…": source } map. */
function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "i18n-literals-"));
  for (const [rel, data] of Object.entries(files)) {
    const p = path.join(root, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, data);
  }
  return { src: path.join(root, "src"), baseline: path.join(root, "baseline.json"), root };
}

function cli(t, ...args) {
  const r = spawnSync(process.execPath, [CLI, "--src", t.src, "--baseline", t.baseline, ...args], { encoding: "utf8" });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

const BUTTON = `export function B() {\n  return <button aria-label="Close panel">Save changes</button>;\n}\n`;
const STATUS = `export function S({ n }: { n: number }) {\n  return (\n    <p title="Status">\n      Showing first {n} rows\n    </p>\n  );\n}\n`;

test("counts JSX text and user-facing attributes, not code", () => {
  assert.deepEqual(literalsIn(BUTTON), ["Save changes", "Close panel"]);
  assert.deepEqual(literalsIn(STATUS), ["Showing first", "Status"]);
  // lower-case text, an arrow body, a non-UI attribute and a translated label are not counted
  const quiet = `const f = () => Foo.bar();\nexport const X = () => <div className="Big" data-x="Y">{t("title")} ok <span>lower</span></div>;\n`;
  assert.deepEqual(literalsIn(quiet), []);
});

test("scans .tsx only, without tests, fixtures or src/app/dev", () => {
  assert.equal(scanned("src/components/chat/A.tsx"), true);
  assert.equal(scanned("src/components/chat/a.ts"), false);
  assert.equal(scanned("src/components/chat/__tests__/A.tsx"), false);
  assert.equal(scanned("src/components/chat/A.test.tsx"), false);
  assert.equal(scanned("src/app/dev/page.tsx"), false);
  assert.equal(groupOf("src/components/chat/deep/A.tsx"), "src/components/chat");
  assert.equal(groupOf("src/app/page.tsx"), "src/app");
});

test("scan groups counts by directory", () => {
  const t = tree({
    "src/components/chat/A.tsx": BUTTON,
    "src/components/chat/inner/B.tsx": STATUS,
    "src/app/admin/page.tsx": BUTTON,
    "src/app/dev/page.tsx": BUTTON,
    "src/components/chat/__tests__/A.test.tsx": BUTTON,
  });
  const { counts, files } = scan(t.src);
  assert.deepEqual(counts, { "src/app/admin": 2, "src/components/chat": 4 });
  assert.equal(files, 3);
});

test("compare reports increases and decreases per directory", () => {
  const { increased, decreased } = compare({ a: 3, b: 1, c: 2 }, { a: 2, b: 4, d: 1 });
  assert.deepEqual(increased, [
    { dir: "a", before: 2, after: 3 },
    { dir: "c", before: 0, after: 2 },
  ]);
  assert.deepEqual(decreased, [
    { dir: "b", before: 4, after: 1 },
    { dir: "d", before: 1, after: 0 },
  ]);
});

test("CLI: unchanged passes; an increase fails and names the directory", () => {
  const t = tree({ "src/components/chat/A.tsx": BUTTON });
  assert.equal(cli(t, "--update").code, 0);
  assert.equal(cli(t).code, 0);

  fs.writeFileSync(path.join(t.src, "components/chat/B.tsx"), STATUS);
  const r = cli(t);
  assert.equal(r.code, 1);
  assert.match(r.err, /src\/components\/chat: 2 → 4/);
});

test("CLI: a decrease passes and is reported; --update lowers the baseline", () => {
  const t = tree({ "src/components/chat/A.tsx": BUTTON, "src/app/admin/page.tsx": BUTTON });
  cli(t, "--update");
  fs.writeFileSync(path.join(t.src, "components/chat/A.tsx"), `export const A = () => <button>{t("save")}</button>;\n`);

  const r = cli(t);
  assert.equal(r.code, 0);
  assert.match(r.out, /src\/components\/chat: 2 → 0/);
  assert.deepEqual(readBaseline(t.baseline), { "src/app/admin": 2, "src/components/chat": 2 }, "a check never writes");

  assert.equal(cli(t, "--update").code, 0);
  assert.deepEqual(readBaseline(t.baseline), { "src/app/admin": 2 });
  assert.equal(JSON.parse(fs.readFileSync(t.baseline, "utf8")).total, 2);
});

test("CLI: --update refuses to raise the baseline without --allow-increase", () => {
  const t = tree({ "src/components/chat/A.tsx": BUTTON });
  cli(t, "--update");
  fs.writeFileSync(path.join(t.src, "components/chat/B.tsx"), BUTTON);

  const refused = cli(t, "--update");
  assert.equal(refused.code, 1);
  assert.match(refused.err, /Refusing/);
  assert.deepEqual(readBaseline(t.baseline), { "src/components/chat": 2 });

  assert.equal(cli(t, "--update", "--allow-increase").code, 0);
  assert.deepEqual(readBaseline(t.baseline), { "src/components/chat": 4 });
});
