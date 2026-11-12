"use client";

// The message layer (multi-language.md §3). Adapted from the AIPLA fork's
// 1.1.108 layer, without that fork's activity/researcher/bilingual rules.
//
// `next-intl` does the ICU work (interpolation, plurals, rich text); the locale
// comes from a small context of ours rather than next-intl's provider, because
// the locale is a client-side preference and not a route segment — the design
// rules out path-prefixed locales (a shared link must not change shape).
//
//     const t = useT("SettingsPage");
//     <h1>{t("title")}</h1>
//     <p>{t("hello", { name })}</p>
//     <p>{t.rich("help", { code: (c) => <code>{c}</code> })}</p>
//
// With NO provider (a unit test, an embed) everything renders in the source
// locale — graceful degradation over a hard dependency (axiom 5).

import { createTranslator } from "next-intl";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { SOURCE_LOCALE, type Locale } from "./locale";
import { GENERATED_CATALOG, mergedMessages, type Catalog, type MessageKey, type Namespace } from "./messages";

export * from "./locale";
export { localizedField, pickLocale, pickVariant, resolveLocalized, type LocaleVariants, type Localized, type LocalizedMap } from "./resolve";
export type { Catalog, MessageKey, Messages, Namespace } from "./messages";
export { formatterFor, regionalLocale, useFormat, type DateInput, type Formatter, type RelativeOptions } from "./format";

interface LocaleState {
  locale: Locale;
  defaultLocale: Locale;
}

const LocaleContext = createContext<LocaleState>({ locale: SOURCE_LOCALE, defaultLocale: SOURCE_LOCALE });
const CatalogContext = createContext<Catalog>(GENERATED_CATALOG);

type Values = Record<string, string | number | Date>;
/** Values for `t.rich`: plain values plus tag renderers, e.g.
 *  `{ code: (chunks) => <code>{chunks}</code> }` for `"… <code>{x}</code> …"`. */
type RichValues = Record<string, string | number | Date | ((chunks: ReactNode) => ReactNode)>;

export interface Translate<N extends Namespace> {
  (key: MessageKey<N>, values?: Values): string;
  /** For a sentence with markup inside it — keeps the sentence whole for the
   *  translator instead of splitting it around a `<code>` or a link. */
  rich(key: MessageKey<N>, values?: RichValues): ReactNode;
}

type Untyped = ((key: string, values?: Values) => string) & { rich: (key: string, values?: RichValues) => ReactNode };

/** Non-hook form, for code outside a component (label helpers, tests). */
export function translate<N extends Namespace>(
  namespace: N,
  locale: Locale = SOURCE_LOCALE,
  defaultLocale: Locale = SOURCE_LOCALE,
  catalog: Catalog = GENERATED_CATALOG,
): Translate<N> {
  const t = createTranslator({
    locale,
    messages: mergedMessages(catalog, locale, defaultLocale),
    namespace,
    // A key missing from every locale is a compile error (typed keys) or a
    // parity-test failure; at runtime it shows the key rather than throwing.
    onError: () => {},
    getMessageFallback: ({ key }: { key: string }) => key,
  } as never) as unknown as Untyped;
  const fn = ((key, values) => t(key, values)) as Translate<N>;
  fn.rich = (key, values) => t.rich(key, values);
  return fn;
}

/** The locale in force for this subtree (the source locale with no provider). */
export function useLocale(): Locale {
  return useContext(LocaleContext).locale;
}

/** Translator for one namespace. */
export function useT<N extends Namespace>(namespace: N): Translate<N> {
  const { locale, defaultLocale } = useContext(LocaleContext);
  const catalog = useContext(CatalogContext);
  return useMemo(() => translate(namespace, locale, defaultLocale, catalog), [namespace, locale, defaultLocale, catalog]);
}

/** Supply a different catalog to a subtree — tests and previews. */
export function CatalogProvider({ catalog, children }: { catalog: Catalog; children: ReactNode }) {
  return <CatalogContext.Provider value={catalog}>{children}</CatalogContext.Provider>;
}

interface LocaleProviderProps {
  locale: Locale;
  /** The deployment default, for the fallback chain. Inherited when omitted. */
  defaultLocale?: Locale;
  /** Set `<html lang>` while mounted. Only the surface that OWNS the page
   *  should pass this — the person's setting at the root, or a fork's screen
   *  whose content has its own language — not an embedded preview. */
  syncHtmlLang?: boolean;
  children: ReactNode;
}

// `<html lang>` belongs to the DEEPEST provider that claims it. React runs
// child effects before parent effects, so "last effect wins" would hand it back
// to the root; claims are registered with their depth instead. (From AIPLA.)
const LangDepthContext = createContext(0);
const htmlLangClaims = new Map<number, { depth: number; lang: Locale }>();
let nextClaimId = 0;

function applyHtmlLang() {
  if (typeof document === "undefined") return;
  let best: { depth: number; lang: Locale } | null = null;
  for (const c of htmlLangClaims.values()) if (!best || c.depth >= best.depth) best = c;
  if (best) document.documentElement.lang = best.lang;
}

/**
 * Pin the locale for a subtree. Nestable: a fork wraps a screen whose content
 * has its own language (AIPLA's activity language) in one, with no template
 * change (design §9).
 */
export function LocaleProvider({ locale, defaultLocale, syncHtmlLang = false, children }: LocaleProviderProps) {
  const parent = useContext(LocaleContext);
  const depth = useContext(LangDepthContext) + 1;
  const value = useMemo(
    () => ({ locale, defaultLocale: defaultLocale ?? parent.defaultLocale }),
    [locale, defaultLocale, parent.defaultLocale],
  );
  useEffect(() => {
    if (!syncHtmlLang) return;
    const id = nextClaimId++;
    htmlLangClaims.set(id, { depth, lang: locale });
    applyHtmlLang();
    return () => {
      htmlLangClaims.delete(id);
      applyHtmlLang();
    };
  }, [locale, syncHtmlLang, depth]);
  return (
    <LangDepthContext.Provider value={depth}>
      <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
    </LangDepthContext.Provider>
  );
}
