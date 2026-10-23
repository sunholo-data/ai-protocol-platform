#!/usr/bin/env node
/**
 * node scripts/user-guide/shots/run.mjs <page> [--base http://127.0.0.1:3470] [--only name,name]
 *
 * Runs <page>.shots.mjs against a LOCAL frontend (scripts/user-guide/shots.sh
 * starts one) and writes docs/user-guide/img/<page>/*.png. Lists any API call
 * the fixture didn't answer — those were refused, and are the gap to fill.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { aroundShooter, open, shooter, tidy } from "./lib.mjs";
import { library } from "./fixtures.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../..");
const args = process.argv.slice(2);
const pageName = args[0];
const flag = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
if (!pageName) {
  console.error("usage: run.mjs <page> [--base URL] [--only a,b]");
  process.exit(2);
}
const base = flag("--base", "http://127.0.0.1:3470");
const only = new Set((flag("--only", "") || "").split(",").filter(Boolean));
const out = path.join(root, "docs/user-guide/img", pageName);
fs.mkdirSync(out, { recursive: true });

const mod = await import(`./${pageName}.shots.mjs`);
const steps = mod.default;
const lib = library();
// A page may add its own synthetic API answers (export function fixture(lib) ->
// handle(route) -> handled?), tried before the shared library. Keeps each page's
// data next to its shots instead of growing one shared fixture.
const extra = mod.fixture ? mod.fixture(lib) : null;
const handle = async (route) => (extra && (await extra(route))) || lib.handle(route);
const { browser, page, unhandled } = await open({ base, handle });
const shot = shooter(page, out);
let failed = 0;
try {
  const skip = (file) => only.size && !only.has(file);
  await steps({
    page,
    base,
    lib,
    tidy: () => tidy(page),
    shot: (file, ...rest) => (skip(file) ? null : shot(file, ...rest)),
    /** around(column)(file, ...locators): a tight crop inside `column` (e.g. the sidebar). */
    around: (column) => {
      const s = aroundShooter(page, out, column);
      return (file, ...rest) => (skip(file) ? null : s(file, ...rest));
    },
  });
} catch (err) {
  failed = 1;
  console.error(`\n${pageName}: a step failed — ${err.message.split("\n")[0]}`);
  await page.screenshot({ path: path.join(out, "_failure.png") }).catch(() => {});
} finally {
  await browser.close();
}
if (unhandled.size) console.log(`\nrefused (not in the fixture):\n  ${[...unhandled].join("\n  ")}`);
process.exit(failed);
