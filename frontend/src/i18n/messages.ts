// The interface catalog: what scripts/i18n/bundle.mjs merged from the template
// (frontend/messages/<locale>/) and any fork overlay
// (docs/customers/<c>/messages/<locale>/). Design: multi-language.md §3–4.
//
// `generated/catalog.json` is a build output (gitignored), written by npm
// predev / prebuild / pretest / pretypecheck and by the Cloud Build bundle step.
// If an import of it fails, run `node ../scripts/i18n/bundle.mjs`.

import CATALOG from "./generated/catalog.json";
import { LOCALE_CONFIG, SOURCE_LOCALE, languageOf, localeChain, type Locale, type LocaleConfig } from "./locale";

/** Keys are typed from the SOURCE catalog: a typo is a compile error. */
type SourceCatalog = (typeof CATALOG)["en"];
export type Namespace = keyof SourceCatalog & string;
export type MessageKey<N extends Namespace> = keyof SourceCatalog[N] & string;

export type Messages = Record<string, Record<string, string>>;
export type Catalog = Record<Locale, Messages>;

export const GENERATED_CATALOG = CATALOG as unknown as Catalog;

/** Configured locales with no catalog for them or their language. */
export function uncoveredLocales(config: LocaleConfig, catalog: Catalog): Locale[] {
  return config.locales.filter((l) => !catalog[l] && !catalog[languageOf(l)]);
}

// A locale offered with no catalog would silently show the source language —
// almost always a typo in NEXT_PUBLIC_LOCALES. Fail where it's noticed: this
// module loads on every page, so `next build` (prerender) and `next dev` stop.
const uncovered = uncoveredLocales(LOCALE_CONFIG, GENERATED_CATALOG);
if (uncovered.length) {
  throw new Error(
    `NEXT_PUBLIC_LOCALES lists ${uncovered.join(", ")} but there is no catalog for it. ` +
      `Add frontend/messages/<locale>/ or docs/customers/<c>/messages/<locale>/, then run scripts/i18n/bundle.mjs.`,
  );
}

const merged = new WeakMap<Catalog, Map<string, Messages>>();

/**
 * Every namespace and key the source has, filled from the most specific locale
 * that has it (chain: locale → its language → default → source). Merging up
 * front means a missing translation still FORMATS — values interpolated, plurals
 * applied — instead of surfacing a raw fallback string or a key.
 */
export function mergedMessages(catalog: Catalog, locale: Locale, defaultLocale: Locale): Messages {
  let byKey = merged.get(catalog);
  if (!byKey) merged.set(catalog, (byKey = new Map()));
  const cacheKey = `${locale}|${defaultLocale}`;
  const hit = byKey.get(cacheKey);
  if (hit) return hit;
  const out: Messages = {};
  for (const l of [...localeChain(locale, defaultLocale)].reverse()) {
    for (const [ns, table] of Object.entries(catalog[l] ?? {})) out[ns] = { ...(out[ns] ?? {}), ...table };
  }
  byKey.set(cacheKey, out);
  return out;
}

export { SOURCE_LOCALE };
