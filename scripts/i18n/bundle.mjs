#!/usr/bin/env node
/**
 * Bundle the interface catalogs.
 *
 *   node scripts/i18n/bundle.mjs [--check]
 *
 * Merges the template catalogs (frontend/messages/<locale>/<area>.json) with any
 * fork overlay (docs/customers/<c>/messages/<locale>/<area>.json) and writes
 * frontend/src/i18n/generated/catalog.json — a build output (gitignored) that
 * the message layer imports. Same arrangement as scripts/user-guide/bundle.mjs,
 * so a fork learns one overlay pattern. Design: docs/design/v6.43.0/multi-language.md §4.
 *
 * Each area file's top-level keys are NAMESPACES (by convention the component
 * name); each namespace is a flat object of ICU message strings.
 *
 * An overlay can:
 *   - add a locale            (docs/customers/acme/messages/da/settings.json)
 *   - add namespaces          (its own screens)
 *   - override template keys  (its own terminology; every override is listed)
 *
 * It fails on: invalid JSON, a non-flat namespace, a non-string message,
 * unbalanced ICU braces, one namespace claimed by two area files of the same
 * layer, and a key an overlay adds to a TEMPLATE namespace that the template
 * doesn't have (a typo that would otherwise be silently unused).
 *
 * No dependencies beyond Node: it runs in a bare node image in Cloud Build.
 * Full ICU parsing is the vitest parity check's job (src/i18n/__tests__).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const TEMPLATE_DIR = path.join(ROOT, "frontend/messages");
const CUSTOMERS_DIR = path.join(ROOT, "docs/customers");
const OUT = path.join(ROOT, "frontend/src/i18n/generated/catalog.json");
export const SOURCE_LOCALE = "en";
const TAG = /^[a-z]{2,3}(?:-(?:[A-Z]{2}|\d{3}))?$/;

export class CatalogError extends Error {}

/** `{` and `}` must balance; `'{'` style ICU quoting is honoured. */
export function bracesBalanced(msg) {
  let depth = 0;
  let quoted = false;
  for (let i = 0; i < msg.length; i++) {
    const c = msg[i];
    if (c === "'") {
      if (msg[i + 1] === "'") {
        i++;
        continue;
      }
      // A quote only opens a literal run before a syntax character.
      if (quoted || "{}#|".includes(msg[i + 1] ?? "")) quoted = !quoted;
      continue;
    }
    if (quoted) continue;
    if (c === "{") depth++;
    else if (c === "}" && --depth < 0) return false;
  }
  return depth === 0;
}

/**
 * Read one layer (template or one customer's overlay): `{locale: {ns: {key: msg}}}`
 * plus where each namespace came from.
 */
export function readLayer(dir, label) {
  const messages = {};
  const origin = {};
  if (!fs.existsSync(dir)) return { messages, origin };
  for (const locale of fs.readdirSync(dir).sort()) {
    const ldir = path.join(dir, locale);
    if (!fs.statSync(ldir).isDirectory()) continue;
    if (!TAG.test(locale)) throw new CatalogError(`${label}: "${locale}" is not a locale tag (e.g. en, da, pt-BR)`);
    messages[locale] = {};
    origin[locale] = {};
    for (const file of fs.readdirSync(ldir).sort()) {
      if (!file.endsWith(".json")) continue;
      const where = path.relative(ROOT, path.join(ldir, file));
      let data;
      try {
        data = JSON.parse(fs.readFileSync(path.join(ldir, file), "utf8"));
      } catch (err) {
        throw new CatalogError(`${where}: invalid JSON (${err.message})`);
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new CatalogError(`${where}: must be an object of namespaces`);
      for (const [ns, table] of Object.entries(data)) {
        if (!table || typeof table !== "object" || Array.isArray(table)) throw new CatalogError(`${where}: namespace "${ns}" must be a flat object of messages`);
        if (origin[locale][ns]) throw new CatalogError(`${where}: namespace "${ns}" is already defined in ${origin[locale][ns]}`);
        for (const [key, msg] of Object.entries(table)) {
          if (typeof msg !== "string") throw new CatalogError(`${where}: ${ns}.${key} must be a string`);
          if (!bracesBalanced(msg)) throw new CatalogError(`${where}: ${ns}.${key} has unbalanced { } — ${JSON.stringify(msg)}`);
        }
        origin[locale][ns] = where;
        messages[locale][ns] = { ...table };
      }
    }
  }
  return { messages, origin };
}

/** Merge template + overlays. Returns `{catalog, overrides, added, locales}`. */
export function build({ templateDir = TEMPLATE_DIR, customersDir = CUSTOMERS_DIR } = {}) {
  const template = readLayer(templateDir, "frontend/messages");
  if (!template.messages[SOURCE_LOCALE]) throw new CatalogError(`frontend/messages/${SOURCE_LOCALE}/ is missing — the source catalog`);
  const templateNs = new Set(Object.values(template.messages).flatMap((l) => Object.keys(l)));
  const sourceKeys = (ns) => new Set(Object.keys(template.messages[SOURCE_LOCALE][ns] ?? {}));

  const catalog = structuredClone(template.messages);
  const overrides = [];
  const added = [];
  const claimedBy = {}; // locale -> ns -> overlay file, for namespaces new to the template

  const customers = fs.existsSync(customersDir)
    ? fs.readdirSync(customersDir).sort().filter((c) => fs.statSync(path.join(customersDir, c)).isDirectory())
    : [];
  for (const c of customers) {
    const overlay = readLayer(path.join(customersDir, c, "messages"), `docs/customers/${c}/messages`);
    for (const [locale, namespaces] of Object.entries(overlay.messages)) {
      catalog[locale] ??= {};
      claimedBy[locale] ??= {};
      for (const [ns, table] of Object.entries(namespaces)) {
        const where = overlay.origin[locale][ns];
        if (templateNs.has(ns)) {
          // Translating or rewording a template namespace: only keys the template has.
          const known = sourceKeys(ns);
          for (const [key, msg] of Object.entries(table)) {
            if (!known.has(key)) throw new CatalogError(`${where}: ${ns}.${key} — the template's ${ns} has no key "${key}"`);
            if (template.messages[locale]?.[ns]?.[key] !== undefined) overrides.push({ locale, key: `${ns}.${key}`, file: where });
          }
          catalog[locale][ns] = { ...(catalog[locale][ns] ?? {}), ...table };
        } else {
          if (claimedBy[locale][ns]) throw new CatalogError(`${where}: namespace "${ns}" is already defined in ${claimedBy[locale][ns]}`);
          claimedBy[locale][ns] = where;
          catalog[locale][ns] = { ...table };
          if (locale === SOURCE_LOCALE) added.push(ns);
        }
      }
    }
  }
  return { catalog, overrides, added, locales: Object.keys(catalog).sort() };
}

export function write(result, out = OUT) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  // Source locale first, so the imported JSON's type is led by the source catalog.
  const ordered = { [SOURCE_LOCALE]: result.catalog[SOURCE_LOCALE] };
  for (const l of result.locales) if (l !== SOURCE_LOCALE) ordered[l] = result.catalog[l];
  fs.writeFileSync(out, JSON.stringify(ordered, null, 2) + "\n");
}

function main() {
  if (!fs.existsSync(TEMPLATE_DIR) || !fs.existsSync(path.join(ROOT, "docs"))) {
    // Inside an image build context there is no docs/ — the bundle was made before.
    console.log("i18n: no repo checkout here; using the existing bundle");
    return;
  }
  const result = build();
  const keys = Object.values(result.catalog[SOURCE_LOCALE]).reduce((n, t) => n + Object.keys(t).length, 0);
  for (const o of result.overrides) console.log(`i18n: override ${o.locale} ${o.key} (${o.file})`);
  if (process.argv.includes("--check")) {
    console.log(`i18n: ${result.locales.join(", ")} OK (${keys} source message(s))`);
    return;
  }
  write(result);
  console.log(`i18n: bundled ${result.locales.join(", ")} — ${keys} source message(s), ${result.overrides.length} override(s)`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (err) {
    console.error(`i18n: ${err.message}`);
    process.exit(1);
  }
}
