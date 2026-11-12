"use client";

// Dates, numbers and relative times in the person's locale (multi-language.md §7).
//
//     const fmt = useFormat();
//     fmt.date(iso)                          // 10/11/2026 (en-GB) · 10.11.2026 (da)
//     fmt.dateTime(ts, { timeZoneName: "short" })
//     fmt.relative(iso)                      // "5m ago" · "5 min. siden"
//     fmt.number(4000)                       // 4,000 · 4.000
//
// This is infrastructure rather than translation: without it a Danish
// interface would still print `11/10/2026`. Everything outside src/i18n goes
// through here — ESLint (frontend/.eslintrc.json) rejects a direct
// `toLocale*String`, `Intl.DateTimeFormat`/`NumberFormat`/`RelativeTimeFormat`
// or an `"en-US"` literal anywhere else.
//
// Which Intl tag. The interface locale decides the LANGUAGE of a format; the
// browser may still refine its REGION. An English interface in a British
// browser keeps day/month order and the 24-hour clock, exactly as the old
// `toLocaleDateString()` calls did (`regionalLocale`). A browser in another
// language contributes nothing, so a Danish browser on an English interface
// now gets English formats rather than Danish ones mixed into English text.
//
// The methods call `Date#toLocale*String` / `Number#toLocaleString` underneath
// rather than `new Intl.DateTimeFormat`, because those keep their own
// defaults: `dateTime(d)` with no options shows date AND time, and
// `{ timeZoneName: "short" }` alone still gets both — so a migrated call
// renders the same string it did before.

import { useMemo } from "react";

import { useLocale } from "./index";
import { SOURCE_LOCALE, languageOf, type Locale } from "./locale";

export type DateInput = Date | number | string;

export interface RelativeOptions {
  /** Count seconds under a minute ("12s ago", "just now" under 5 s). Default:
   *  anything under a minute is "just now". */
  seconds?: boolean;
  /** From this many days on, show the date instead. Default 30. */
  maxDays?: number;
  /** How that date is shown. Default day and short month ("10 Nov"). */
  dateOptions?: Intl.DateTimeFormatOptions;
}

export interface Formatter {
  /** The tag handed to Intl, e.g. `en-GB` for English in a British browser. */
  readonly locale: string;
  /** Date only; numeric day/month/year unless `opts` say otherwise. */
  date(d: DateInput, opts?: Intl.DateTimeFormatOptions): string;
  /** Time only; hours, minutes and seconds unless `opts` say otherwise. */
  time(d: DateInput, opts?: Intl.DateTimeFormatOptions): string;
  /** Date and time unless `opts` pick fields. */
  dateTime(d: DateInput, opts?: Intl.DateTimeFormatOptions): string;
  /** Compact "time ago" for list rows; the date once it is old. `""` for a
   *  missing or unparseable value. */
  relative(d: DateInput | null | undefined, now?: number, opts?: RelativeOptions): string;
  number(n: number, opts?: Intl.NumberFormatOptions): string;
  /** A file size: `512 B`, `1.5 kB`, `12 MB` (decimal units, as Intl names them). */
  bytes(n: number): string;
}

let runtimeDefault: string | undefined;

/** The runtime's own locale — the browser's language in a browser. */
function runtimeLocale(): string {
  if (runtimeDefault === undefined) {
    try {
      runtimeDefault = new Intl.DateTimeFormat().resolvedOptions().locale;
    } catch {
      runtimeDefault = SOURCE_LOCALE;
    }
  }
  return runtimeDefault;
}

/**
 * The tag to format with: a bare language takes the runtime's region when the
 * runtime speaks that language (`en` + a British browser → `en-GB`); a tag
 * that names a region, or a runtime in another language, is left as it is.
 */
export function regionalLocale(locale: Locale, runtime: string = runtimeLocale()): string {
  if (locale.includes("-")) return locale;
  return runtime && languageOf(runtime) === locale ? runtime : locale;
}

function toDate(d: DateInput): Date {
  return d instanceof Date ? d : new Date(d);
}

// The template's compact English forms. ICU's own narrow English is close but
// varies by version ("5m ago" today, "5 min. ago" in older engines), and these
// strings predate the layer, so English keeps them; every other language gets
// Intl.RelativeTimeFormat.
const EN_RELATIVE: Record<"second" | "minute" | "hour" | "day", string> = {
  second: "s",
  minute: "m",
  hour: "h",
  day: "d",
};

const DECIMAL_UNITS = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"] as const;

function build(tag: string): Formatter {
  let rtf: Intl.RelativeTimeFormat | undefined;
  const english = languageOf(tag) === SOURCE_LOCALE;

  function ago(value: number, unit: "second" | "minute" | "hour" | "day"): string {
    if (english) return `${value}${EN_RELATIVE[unit]} ago`;
    rtf ??= new Intl.RelativeTimeFormat(tag, { numeric: "auto", style: "narrow" });
    return rtf.format(-value, unit);
  }

  function justNow(): string {
    if (english) return "just now";
    rtf ??= new Intl.RelativeTimeFormat(tag, { numeric: "auto", style: "narrow" });
    return rtf.format(0, "second");
  }

  const fmt: Formatter = {
    locale: tag,
    date: (d, opts) => toDate(d).toLocaleDateString(tag, opts),
    time: (d, opts) => toDate(d).toLocaleTimeString(tag, opts),
    dateTime: (d, opts) => toDate(d).toLocaleString(tag, opts),
    relative(d, now = Date.now(), opts = {}) {
      if (d === null || d === undefined || d === "") return "";
      const t = toDate(d).getTime();
      if (Number.isNaN(t)) return "";
      const { seconds = false, maxDays = 30, dateOptions = { day: "numeric", month: "short" } } = opts;
      const s = Math.floor(Math.max(0, now - t) / 1000);
      if (s < (seconds ? 5 : 60)) return justNow();
      if (s < 60) return ago(s, "second");
      const m = Math.floor(s / 60);
      if (m < 60) return ago(m, "minute");
      const h = Math.floor(m / 60);
      if (h < 24) return ago(h, "hour");
      const days = Math.floor(h / 24);
      if (days < maxDays) return ago(days, "day");
      return fmt.date(t, dateOptions);
    },
    number: (n, opts) => n.toLocaleString(tag, opts),
    bytes(n) {
      let i = 0;
      let v = Math.abs(n);
      while (v >= 1000 && i < DECIMAL_UNITS.length - 1) {
        v /= 1000;
        i += 1;
      }
      return (Math.sign(n) * v).toLocaleString(tag, {
        style: "unit",
        unit: DECIMAL_UNITS[i],
        unitDisplay: "short",
        maximumFractionDigits: i === 0 || v >= 10 ? 0 : 1,
      });
    },
  };
  return fmt;
}

const cache = new Map<string, Formatter>();

/**
 * A formatter for exactly this tag — the non-hook form, for code outside a
 * component (pass it `regionalLocale(locale)` to match `useFormat()`).
 */
export function formatterFor(locale: string): Formatter {
  let f = cache.get(locale);
  if (!f) {
    f = build(locale);
    cache.set(locale, f);
  }
  return f;
}

/**
 * The formatter for the current locale (`useLocale()`), refined by the
 * browser's region. Pass `{ browserRegion: false }` for the language's own
 * conventions whatever the browser says — what a call that used to hard-code
 * `"en"` rather than the browser default showed.
 */
export function useFormat({ browserRegion = true }: { browserRegion?: boolean } = {}): Formatter {
  const locale = useLocale();
  return useMemo(() => formatterFor(browserRegion ? regionalLocale(locale) : locale), [locale, browserRegion]);
}
