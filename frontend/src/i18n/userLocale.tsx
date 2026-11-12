"use client";

// The person's interface language (multi-language.md §2).
//
// Resolution: their explicit choice (the language switch, remembered in THIS
// browser) → a fork-registered default resolver → the deployment default.
// Never a guess from the browser. Per browser by decision (2026-11-10): the
// choice does not follow a person across devices; a fork that needs that
// registers a resolver that reads the profile.
//
// localStorage because this is a per-browser preference; every access is
// guarded — storage can be missing or throw (private mode, embedded frames).

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { LocaleProvider } from "./index";
import { LOCALE_CONFIG, normaliseLocale, type Locale, type LocaleConfig } from "./locale";

export const USER_LOCALE_KEY = `${process.env.NEXT_PUBLIC_APP_SLUG || "platform"}.uiLocale`;

type DefaultResolver = (config: LocaleConfig) => Locale | null | undefined;
let defaultResolver: DefaultResolver | null = null;

/**
 * Register how a person with NO explicit choice gets their default — by role,
 * profile, organisation, or browser if a fork wants that. Called on the client
 * after mount; return null to use the deployment default. An explicit choice
 * always wins. A resolver that needs React state should instead wrap its screens
 * in a nested `<LocaleProvider>`.
 */
export function setDefaultLocaleResolver(fn: DefaultResolver | null): void {
  defaultResolver = fn;
}

function offered(config: LocaleConfig, raw: string | null | undefined): Locale | null {
  const tag = normaliseLocale(raw);
  return tag && config.locales.includes(tag) ? tag : null;
}

function readStored(config: LocaleConfig): Locale | null {
  try {
    return typeof localStorage === "undefined" ? null : offered(config, localStorage.getItem(USER_LOCALE_KEY));
  } catch {
    return null;
  }
}

function writeStored(locale: Locale): void {
  try {
    localStorage.setItem(USER_LOCALE_KEY, locale);
  } catch {
    // Storage unavailable: the choice holds for this page view only.
  }
}

interface UserLocaleValue {
  /** The locale in force for this person. */
  locale: Locale;
  /** What the switch offers; one entry means no switch. */
  locales: readonly Locale[];
  /** True once the person has chosen in this browser. */
  explicit: boolean;
  setLocale: (locale: Locale) => void;
}

const UserLocaleContext = createContext<UserLocaleValue>({
  locale: LOCALE_CONFIG.defaultLocale,
  locales: LOCALE_CONFIG.locales,
  explicit: false,
  setLocale: () => {},
});

export function UserLocaleProvider({ children, config = LOCALE_CONFIG }: { children: ReactNode; config?: LocaleConfig }) {
  // Both start null so the server render and the first client render agree
  // (the deployment default); the browser's values arrive after mount.
  const [stored, setStored] = useState<Locale | null>(null);
  const [resolvedDefault, setResolvedDefault] = useState<Locale | null>(null);

  useEffect(() => {
    if (config.locales.length < 2) return;
    setStored(readStored(config));
    setResolvedDefault(offered(config, defaultResolver?.(config)));
    // Another tab changed the language: follow it.
    const onStorage = (e: StorageEvent) => {
      if (e.key === USER_LOCALE_KEY) setStored(offered(config, e.newValue));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [config]);

  const setLocale = useCallback(
    (next: Locale) => {
      const tag = offered(config, next);
      if (!tag) return;
      writeStored(tag);
      setStored(tag);
    },
    [config],
  );

  const locale = stored ?? resolvedDefault ?? config.defaultLocale;
  const value = useMemo(
    () => ({ locale, locales: config.locales, explicit: stored !== null, setLocale }),
    [locale, config.locales, stored, setLocale],
  );
  return (
    <UserLocaleContext.Provider value={value}>
      <LocaleProvider locale={locale} defaultLocale={config.defaultLocale} syncHtmlLang>
        {children}
      </LocaleProvider>
    </UserLocaleContext.Provider>
  );
}

/** The person's locale, what's offered, and the setter the language switch uses. */
export function useUserLocale(): UserLocaleValue {
  return useContext(UserLocaleContext);
}
