// Resolving localised CONTENT (not interface strings — those go through useT).
//
// Two shapes carry a second language anywhere in the platform (design §5):
//
//   a map            { en: "Projects", da: "Projekter" }
//   base + variants  display_name: "General Assistant"
//                    locales: { da: { display_name: "Generel assistent" } }
//
// A map resolves along locale.ts `localeChain`; base + variants tries the
// person's locale and then the base (see localizedField). The backend's
// `pick_locale` (backend/i18n.py) mirrors this; a shared table keeps them equal.
// Exported for forks' own content, so the fallback rule is never re-implemented.

import { LOCALE_CONFIG, SOURCE_LOCALE, languageOf, localeChain, type Locale } from "./locale";

export type LocalizedMap = Readonly<Record<string, string | null | undefined>>;
export type Localized = string | LocalizedMap | null | undefined;

const present = (v: unknown): v is string => typeof v === "string" && v !== "";

/** The first locale in the chain that the map has a non-empty value for; undefined when none. */
export function pickLocale(
  map: LocalizedMap | null | undefined,
  locale: Locale,
  defaultLocale: Locale = LOCALE_CONFIG.defaultLocale,
): string | undefined {
  if (!map) return undefined;
  for (const l of localeChain(locale, defaultLocale)) {
    const v = map[l];
    if (present(v)) return v;
  }
  return undefined;
}

/**
 * A plain string resolves to itself. A map resolves along the chain, then to
 * any value it has, then to "" — never to a key or an error.
 */
export function resolveLocalized(
  value: Localized,
  locale: Locale,
  defaultLocale: Locale = LOCALE_CONFIG.defaultLocale,
): string {
  if (typeof value === "string") return value;
  if (!value) return "";
  return pickLocale(value, locale, defaultLocale) ?? Object.values(value).find(present) ?? "";
}

/**
 * A base value plus per-locale variants (SKILL.md `locales:`).
 *
 * The BASE is the author's own text in whatever language they wrote it — the
 * template's skills are English, a Danish fork's own skills are Danish — and
 * nothing records which. So only the person's locale (then its language) is
 * tried, and otherwise the base wins. Walking on to the deployment default would
 * be wrong: someone who picked English on a Danish-default deployment would get
 * a skill's `da` variant instead of its English base.
 */
export type LocaleVariants = Readonly<Record<string, Readonly<Record<string, string | null | undefined>> | null | undefined>>;

export function localizedField(
  base: string | null | undefined,
  variants: LocaleVariants | null | undefined,
  field: string,
  locale: Locale,
): string {
  if (variants) {
    for (const l of [locale, languageOf(locale)]) {
      const v = variants[l]?.[field];
      if (present(v)) return v;
    }
  }
  return base ?? "";
}

/**
 * Content kept as a source version plus translations in `<locale>/` sibling
 * folders (the user guide; a fork's own pages). The map holds the translations
 * only: walking the chain, reaching the source locale means "the source", so
 * someone who chose English on a Danish-default deployment gets the English
 * page, not the default's translation. undefined → show the source.
 */
export function pickVariant<T>(
  variants: Readonly<Record<string, T>> | null | undefined,
  locale: Locale,
  defaultLocale: Locale = LOCALE_CONFIG.defaultLocale,
): T | undefined {
  if (!variants) return undefined;
  for (const l of localeChain(locale, defaultLocale)) {
    if (l === SOURCE_LOCALE) return undefined;
    if (Object.prototype.hasOwnProperty.call(variants, l)) return variants[l];
  }
  return undefined;
}
