/**
 * Server-side loader for the bundled user guide (frontend/content/user-guide,
 * written by scripts/user-guide/bundle.mjs from docs/user-guide + fork overlays).
 * Read at build time by the static /help pages; nothing here runs in the browser.
 *
 * Translations (multi-language.md §5) sit in `<locale>/` beside the source
 * pages, each folder with its own manifest. The person's language is a browser
 * preference the server never sees, so /help renders the source page plus the
 * translations this deployment can show, and a client component picks one
 * (components/help/LocaleVariant). An English-only deployment gets no variants:
 * the same page as before translations existed.
 */

import fs from "node:fs";
import path from "node:path";

import { LOCALE_CONFIG, SOURCE_LOCALE, languageOf, type Locale } from "@/i18n/locale";

import type { GuideManifest, GuidePageMeta } from "./userGuideParse";

const DIR = path.join(process.cwd(), "content", "user-guide");

const EMPTY: GuideManifest = { tags: [], pages: [] };

/** Bundle names: never a path. */
const SLUG = /^[\w-]+$/;
const LOCALE_TAG = /^[a-z]{2,3}(?:-(?:[A-Z]{2}|\d{3}))?$/;

function readJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export function readManifest(dir: string = DIR): GuideManifest {
  // No bundle (e.g. a checkout that never ran the bundler): /help says so
  // rather than 500ing. `npm run dev` / `build` run the bundler first.
  return readJson(path.join(dir, "manifest.json"), EMPTY);
}

/** One locale's translated page metadata; [] for an unknown or malformed locale. */
export function readLocaleManifest(locale: string, dir: string = DIR): GuidePageMeta[] {
  if (!LOCALE_TAG.test(locale) || locale === SOURCE_LOCALE) return [];
  if (!readManifest(dir).locales?.[locale]) return [];
  return readJson<{ pages?: GuidePageMeta[] }>(path.join(dir, locale, "manifest.json"), {}).pages ?? [];
}

/** A page: the source page, or with `locale` its translation (null when there is none — callers fall back). */
export function readPage(slug: string, dir: string = DIR, locale?: string): { meta: GuidePageMeta; body: string } | null {
  if (!SLUG.test(slug)) return null;
  const translated = locale !== undefined && locale !== SOURCE_LOCALE;
  if (translated && !LOCALE_TAG.test(locale)) return null;
  const meta = (translated ? readLocaleManifest(locale, dir) : readManifest(dir).pages).find((p) => p.slug === slug);
  if (!meta) return null;
  try {
    return { meta, body: fs.readFileSync(path.join(dir, ...(translated ? [locale] : []), `${slug}.md`), "utf8") };
  } catch {
    return null;
  }
}

/**
 * The translated locales this deployment can show: those a configured locale's
 * fallback chain reaches (`pt-BR` reaches `pt`). Empty for an English-only
 * deployment, whatever the bundle holds.
 */
export function shownLocales(manifest: GuideManifest, offered: readonly Locale[] = LOCALE_CONFIG.locales): Locale[] {
  const reach = new Set(offered.flatMap((l) => [l, languageOf(l)]));
  return Object.keys(manifest.locales ?? {})
    .filter((l) => l !== SOURCE_LOCALE && reach.has(l))
    .sort();
}

/** The translations of one page this deployment can show, by locale. */
export function pageTranslations(
  slug: string,
  dir: string = DIR,
  offered: readonly Locale[] = LOCALE_CONFIG.locales,
): { locale: Locale; meta: GuidePageMeta; body: string }[] {
  const manifest = readManifest(dir);
  return shownLocales(manifest, offered)
    .filter((l) => manifest.locales?.[l]?.includes(slug))
    .flatMap((locale) => {
      const page = readPage(slug, dir, locale);
      return page ? [{ locale, ...page }] : [];
    });
}

/** Translated titles and summaries for the index and contents: `{slug: {locale: {title, summary}}}`. */
export function translatedCards(
  dir: string = DIR,
  offered: readonly Locale[] = LOCALE_CONFIG.locales,
): Record<string, Record<Locale, { title: string; summary: string }>> {
  const out: Record<string, Record<Locale, { title: string; summary: string }>> = {};
  for (const locale of shownLocales(readManifest(dir), offered)) {
    for (const p of readLocaleManifest(locale, dir)) {
      (out[p.slug] ??= {})[locale] = { title: p.title, summary: p.summary };
    }
  }
  return out;
}
