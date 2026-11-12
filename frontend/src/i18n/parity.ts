// Catalog parity (multi-language.md §8): what a non-source locale may contain,
// and what it is still missing. Pure, so the CI test and a fork's own checks
// share it.

import { SOURCE_LOCALE, type Locale } from "./locale";
import type { Catalog } from "./messages";

export interface ParityReport {
  /** Fail the build: keys the source doesn't have, or placeholders that differ. */
  errors: string[];
  /** Report only (unless I18N_STRICT=1): source keys a locale hasn't translated yet. */
  missing: Record<Locale, string[]>;
}

/** Argument names and rich-text tag names a message uses: `{name}`, `{n, plural, …}`, `<b>…</b>`. */
export function placeholders(msg: string): string[] {
  const out = new Set<string>();
  for (const m of msg.matchAll(/\{\s*([A-Za-z_][\w]*)\s*[,}]/g)) out.add(m[1]);
  for (const m of msg.matchAll(/<([A-Za-z_][\w-]*)>/g)) out.add(`<${m[1]}>`);
  return [...out].sort();
}

export function checkParity(catalog: Catalog, source: Locale = SOURCE_LOCALE): ParityReport {
  const errors: string[] = [];
  const missing: Record<Locale, string[]> = {};
  const src = catalog[source] ?? {};
  for (const [locale, namespaces] of Object.entries(catalog)) {
    if (locale === source) continue;
    for (const [ns, table] of Object.entries(namespaces)) {
      const srcTable = src[ns];
      if (!srcTable) {
        errors.push(`${locale}: namespace ${ns} is not in the ${source} catalog`);
        continue;
      }
      for (const [key, msg] of Object.entries(table)) {
        if (!(key in srcTable)) {
          errors.push(`${locale}: ${ns}.${key} is not in the ${source} catalog`);
          continue;
        }
        const want = placeholders(srcTable[key]).join(",");
        const got = placeholders(msg).join(",");
        if (want !== got) errors.push(`${locale}: ${ns}.${key} uses {${got}} but ${source} uses {${want}}`);
      }
    }
    const gaps: string[] = [];
    for (const [ns, srcTable] of Object.entries(src)) {
      for (const key of Object.keys(srcTable)) if (namespaces[ns]?.[key] === undefined) gaps.push(`${ns}.${key}`);
    }
    if (gaps.length) missing[locale] = gaps;
  }
  return { errors, missing };
}
