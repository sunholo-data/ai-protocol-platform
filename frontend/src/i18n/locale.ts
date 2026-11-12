// The locale vocabulary and the one resolution rule (multi-language.md §1).
//
// Plain module, no React, so server code, label helpers and tests can use it
// without a client boundary.
//
// Language is a deployment setting plus the person's explicit choice — never a
// guess from `navigator.language` or `Accept-Language` (design non-goal). A fork
// that wants inference registers a resolver (userLocale.tsx).

import { BRANDING } from "@/lib/branding";

/** The language the template's own strings are written in. Every lookup ends here. */
export const SOURCE_LOCALE = "en";

/** A BCP 47 tag, normalised: lower-case language, upper-case region (`pt-BR`). */
export type Locale = string;

const TAG = /^([a-z]{2,3})(?:-([A-Z]{2}|\d{3}))?$/;

/** `"pt_br"`, `"PT-br"` → `"pt-BR"`; anything that isn't a language[-region] tag → null. */
export function normaliseLocale(raw: string | null | undefined): Locale | null {
  if (typeof raw !== "string") return null;
  const parts = raw.trim().replace(/_/g, "-").split("-");
  if (parts.length > 2 || !parts[0]) return null;
  const tag = parts.length === 2 ? `${parts[0].toLowerCase()}-${parts[1].toUpperCase()}` : parts[0].toLowerCase();
  return TAG.test(tag) ? tag : null;
}

/** The language half of a tag: `pt-BR` → `pt`. */
export function languageOf(locale: Locale): Locale {
  return locale.split("-")[0];
}

/** `"da, en,da,bogus"` → `["da", "en"]`. Empty or all-invalid → `[SOURCE_LOCALE]`. */
export function parseLocaleList(raw: string | null | undefined): Locale[] {
  const out: Locale[] = [];
  for (const part of (raw ?? "").split(",")) {
    const tag = normaliseLocale(part);
    if (tag && !out.includes(tag)) out.push(tag);
  }
  return out.length ? out : [SOURCE_LOCALE];
}

export interface LocaleConfig {
  /** What the language switch offers. One entry means no switch. */
  locales: readonly Locale[];
  /** What a person who has never chosen sees. Always one of `locales`. */
  defaultLocale: Locale;
}

/** Build a config from the two settings; a default that isn't offered falls back to the first offered. */
export function localeConfig(localesRaw: string | null | undefined, defaultRaw: string | null | undefined): LocaleConfig {
  const locales = parseLocaleList(localesRaw);
  const wanted = normaliseLocale(defaultRaw);
  return { locales, defaultLocale: wanted && locales.includes(wanted) ? wanted : locales[0] };
}

/** This deployment's config, from NEXT_PUBLIC_LOCALES / NEXT_PUBLIC_DEFAULT_LOCALE (via BRANDING). */
export const LOCALE_CONFIG: LocaleConfig = localeConfig(BRANDING.locales, BRANDING.defaultLocale);

/**
 * The order a lookup tries locales in: the requested one, its language, the
 * deployment default, its language, then the source. Deduplicated.
 *
 *   localeChain("pt-BR", "da") → ["pt-BR", "pt", "da", "en"]
 */
export function localeChain(locale: Locale, defaultLocale: Locale = LOCALE_CONFIG.defaultLocale): Locale[] {
  const out: Locale[] = [];
  for (const l of [locale, languageOf(locale), defaultLocale, languageOf(defaultLocale), SOURCE_LOCALE]) {
    if (l && !out.includes(l)) out.push(l);
  }
  return out;
}
