#!/usr/bin/env node
/**
 * Hard-coded interface string ratchet (multi-language.md §8).
 *
 *   node scripts/i18n/literals.mjs                     # check (make i18n-check)
 *   node scripts/i18n/literals.mjs --update            # lower the baseline
 *   node scripts/i18n/literals.mjs --update --allow-increase
 *
 * Counts literal UI strings per directory in frontend/src and compares them
 * with scripts/i18n/literals-baseline.json. The check fails when any directory
 * has MORE than its baseline, so the migration to catalogs (useT) can only move
 * one way while nobody is working on it. It prints the directories that went
 * down; `--update` writes their lower counts. `--update` refuses to raise a
 * count unless `--allow-increase` is passed (say why in the commit), or there
 * is no baseline yet.
 *
 * The scan is deliberately crude — the same one the design used to size the
 * problem — and counts, in .tsx files (tests, fixtures and src/app/dev/ left
 * out):
 *   - JSX text that starts with a capital letter: `>Save changes<`, `>Delete {`
 *   - aria-label / title / placeholder / alt / label="Capitalised…" attributes
 * A false positive costs nothing as long as it is counted the same way every
 * time; what matters is the direction. Once a directory is migrated, ESLint's
 * react/jsx-no-literals is switched on for it (frontend/.eslintrc.json).
 *
 * No dependencies beyond Node, like scripts/i18n/bundle.mjs.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
export const DEFAULT_SRC = path.join(ROOT, "frontend/src");
export const DEFAULT_BASELINE = path.join(HERE, "literals-baseline.json");

// `>` that closes a tag (not `=>` or `->`), then text starting with a capital,
// up to the next tag or expression.
const JSX_TEXT = /(?<![=\-])>\s*([A-Z][A-Za-z0-9'’,.!?:;…—–()/&% -]{2,}?)\s*(?=[<{])/g;
// A user-facing attribute with a capitalised string value.
const ATTR = /(?<![\w-])(?:aria-label|title|placeholder|alt|label)=(["'])([A-Z][^"'\n]*)\1/g;

/** Whether a path (relative to the frontend dir, `/`-separated) is scanned. */
export function scanned(rel) {
  if (!rel.endsWith(".tsx")) return false;
  if (rel.startsWith("src/app/dev/")) return false;
  if (/(^|\/)(__tests__|__fixtures__|__mocks__)\//.test(rel)) return false;
  if (/\.(test|spec)\.tsx$/.test(rel)) return false;
  return true;
}

/** The literal UI strings in one file's source. */
export function literalsIn(source) {
  const out = [];
  for (const m of source.matchAll(JSX_TEXT)) out.push(m[1].trim());
  for (const m of source.matchAll(ATTR)) out.push(m[2]);
  return out;
}

/** The directory a file counts toward: its first three path segments. */
export function groupOf(rel) {
  return rel.split("/").slice(0, -1).slice(0, 3).join("/");
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else if (entry.isFile()) yield p;
  }
}

/**
 * Count literal strings per directory under `srcDir` (a frontend `src`).
 * Keys are relative to the frontend dir (`src/components/chat`); directories
 * with none are left out.
 */
export function scan(srcDir = DEFAULT_SRC) {
  const base = path.dirname(srcDir);
  const counts = {};
  let files = 0;
  for (const file of walk(srcDir)) {
    const rel = path.relative(base, file).split(path.sep).join("/");
    if (!scanned(rel)) continue;
    const n = literalsIn(fs.readFileSync(file, "utf8")).length;
    if (!n) continue;
    files += 1;
    const group = groupOf(rel);
    counts[group] = (counts[group] ?? 0) + n;
  }
  return { counts: sorted(counts), files };
}

function sorted(obj) {
  return Object.fromEntries(Object.entries(obj).sort(([a], [b]) => a.localeCompare(b)));
}

export function total(counts) {
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

/** What changed per directory: `increased` fails the check, `decreased` can be banked with --update. */
export function compare(counts, baseline) {
  const increased = [];
  const decreased = [];
  for (const dir of new Set([...Object.keys(counts), ...Object.keys(baseline)])) {
    const before = baseline[dir] ?? 0;
    const after = counts[dir] ?? 0;
    if (after > before) increased.push({ dir, before, after });
    else if (after < before) decreased.push({ dir, before, after });
  }
  const byDir = (a, b) => a.dir.localeCompare(b.dir);
  return { increased: increased.sort(byDir), decreased: decreased.sort(byDir) };
}

export function readBaseline(file) {
  if (!fs.existsSync(file)) return {};
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  return data.dirs ?? {};
}

export function writeBaseline(file, counts) {
  const data = {
    _comment:
      "Literal UI strings per directory in frontend/src (scripts/i18n/literals.mjs). " +
      "May only go down: lower it with `node scripts/i18n/literals.mjs --update`.",
    total: total(counts),
    dirs: sorted(counts),
  };
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`);
}

function arg(args, name) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

/** The CLI. Returns the exit code. */
export function main(args = process.argv.slice(2), log = console.log, error = console.error) {
  const src = path.resolve(arg(args, "--src") ?? DEFAULT_SRC);
  const baselineFile = path.resolve(arg(args, "--baseline") ?? DEFAULT_BASELINE);
  const update = args.includes("--update");
  // The first --update creates the baseline; after that it may only go down.
  const allowIncrease = args.includes("--allow-increase") || !fs.existsSync(baselineFile);

  const { counts, files } = scan(src);
  const baseline = readBaseline(baselineFile);
  const { increased, decreased } = compare(counts, baseline);
  const line = ({ dir, before, after }) => `  ${dir}: ${before} → ${after}`;

  if (decreased.length) {
    log(`Fewer hard-coded strings in ${decreased.length} director${decreased.length === 1 ? "y" : "ies"}:`);
    for (const d of decreased) log(line(d));
  }

  if (update) {
    if (increased.length && !allowIncrease) {
      error("Refusing to raise the baseline (pass --allow-increase if that is really intended):");
      for (const d of increased) error(line(d));
      return 1;
    }
    writeBaseline(baselineFile, counts);
    log(`Baseline written: ${total(counts)} literal strings in ${files} files, ${Object.keys(counts).length} directories.`);
    return 0;
  }

  if (increased.length) {
    error("New hard-coded interface strings — put them in a catalog and use useT() (frontend/src/i18n):");
    for (const d of increased) error(line(d));
    error("See docs/design/v6.43.0/multi-language.md §3. To accept an increase: --update --allow-increase.");
    return 1;
  }
  if (decreased.length) log("Run `node scripts/i18n/literals.mjs --update` to lower the baseline.");
  log(`i18n-check: ${total(counts)} literal strings (baseline ${total(baseline)}), no directory went up.`);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
