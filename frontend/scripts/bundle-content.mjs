#!/usr/bin/env node
// Build outputs the app imports, made from repo content outside frontend/:
//   - the user guide        (scripts/user-guide/bundle.mjs)
//   - interface catalogs    (scripts/i18n/bundle.mjs, multi-language.md §4)
// Run from npm predev / prebuild / pretest / pretypecheck. Inside an image build
// context there is no ../scripts — Cloud Build bundled before the image build,
// so each step is skipped when its script is absent.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const only = process.argv.slice(2);
for (const [name, rel] of [["user-guide", "scripts/user-guide/bundle.mjs"], ["i18n", "scripts/i18n/bundle.mjs"]]) {
  if (only.length && !only.includes(name)) continue;
  const script = path.join(repo, rel);
  if (!fs.existsSync(script)) continue;
  const r = spawnSync(process.execPath, [script], { stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status ?? 1);
}
