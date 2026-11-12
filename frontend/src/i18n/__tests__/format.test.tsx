import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { LocaleProvider, formatterFor, regionalLocale, useFormat, type Formatter } from "..";

// Expected values come from Intl itself, so these hold across ICU versions;
// the shape checks (24-hour clock, decimal comma) are what the design promises.

const AT = new Date(2026, 10, 9, 14, 5, 7); // 9 Nov 2026, 14:05:07 local
const HM: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };

function inLocale(locale: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <LocaleProvider locale={locale}>{children}</LocaleProvider>;
  };
}

describe("regionalLocale", () => {
  it("takes the runtime's region when it speaks the same language", () => {
    expect(regionalLocale("en", "en-GB")).toBe("en-GB");
    expect(regionalLocale("da", "da-DK")).toBe("da-DK");
  });
  it("ignores a runtime in another language, and a tag that names its region", () => {
    expect(regionalLocale("en", "da-DK")).toBe("en");
    expect(regionalLocale("pt-BR", "pt-PT")).toBe("pt-BR");
  });
});

describe("useFormat in en", () => {
  let fmt: Formatter;
  beforeEach(() => {
    fmt = renderHook(() => useFormat(), { wrapper: inLocale("en") }).result.current;
  });
  const tag = regionalLocale("en");

  it("formats exactly as the browser-default calls it replaced", () => {
    // Under an English runtime the tag IS the runtime default, so
    // `toLocale*String()` with no locale is the old output.
    expect(fmt.locale).toBe(tag);
    expect(fmt.date(AT)).toBe(AT.toLocaleDateString(tag));
    expect(fmt.dateTime(AT)).toBe(AT.toLocaleString(tag));
    expect(fmt.time(AT)).toBe(AT.toLocaleTimeString(tag));
    expect(fmt.dateTime(AT, { timeZoneName: "short" })).toBe(AT.toLocaleString(tag, { timeZoneName: "short" }));
    expect(fmt.number(4000)).toBe(new Intl.NumberFormat(tag).format(4000));
  });

  it("keeps the compact English relative forms", () => {
    const now = AT.getTime();
    expect(fmt.relative(now - 30_000, now)).toBe("just now");
    expect(fmt.relative(now - 2_000, now, { seconds: true })).toBe("just now");
    expect(fmt.relative(now - 12_000, now, { seconds: true })).toBe("12s ago");
    expect(fmt.relative(now - 5 * 60_000, now)).toBe("5m ago");
    expect(fmt.relative(now - 3 * 3_600_000, now)).toBe("3h ago");
    expect(fmt.relative(now - 2 * 86_400_000, now)).toBe("2d ago");
    const old = now - 40 * 86_400_000;
    expect(fmt.relative(old, now)).toBe(new Date(old).toLocaleDateString(tag, { day: "numeric", month: "short" }));
    expect(fmt.relative(now - 2 * 86_400_000, now, { maxDays: 1, dateOptions: {} })).toBe(
      new Date(now - 2 * 86_400_000).toLocaleDateString(tag),
    );
  });

  it("returns empty for a missing or unparseable time", () => {
    expect(fmt.relative(null)).toBe("");
    expect(fmt.relative(undefined)).toBe("");
    expect(fmt.relative("not a date")).toBe("");
  });

  it("without the browser region, uses the bare language (the old pinned \"en\")", () => {
    const { result: bare } = renderHook(() => useFormat({ browserRegion: false }), { wrapper: inLocale("en") });
    expect(bare.current.locale).toBe("en");
    expect(bare.current.time(AT, HM)).toBe(new Intl.DateTimeFormat("en", HM).format(AT));
  });

  it("formats sizes", () => {
    expect(fmt.bytes(512)).toBe(new Intl.NumberFormat(tag, { style: "unit", unit: "byte", unitDisplay: "short" }).format(512));
    expect(fmt.bytes(1_500_000)).toBe(
      new Intl.NumberFormat(tag, { style: "unit", unit: "megabyte", unitDisplay: "short", maximumFractionDigits: 1 }).format(1.5),
    );
  });

  it("is the source locale with no provider", () => {
    const { result: none } = renderHook(() => useFormat());
    expect(none.current.locale).toBe(tag);
  });
});

describe("useFormat in da", () => {
  let fmt: Formatter;
  beforeEach(() => {
    fmt = renderHook(() => useFormat(), { wrapper: inLocale("da") }).result.current;
  });
  const tag = regionalLocale("da");

  it("uses a 24-hour clock", () => {
    const out = fmt.time(AT, HM);
    expect(out).toBe(new Intl.DateTimeFormat(tag, HM).format(AT));
    expect(out).toMatch(/14[.:]05/);
    expect(out).not.toMatch(/PM/i);
  });

  it("uses a decimal comma", () => {
    const out = fmt.number(1234.5, { minimumFractionDigits: 1 });
    expect(out).toBe(new Intl.NumberFormat(tag, { minimumFractionDigits: 1 }).format(1234.5));
    expect(out).toMatch(/,5$/);
  });

  it("formats dates the Danish way", () => {
    expect(fmt.date(AT)).toBe(new Intl.DateTimeFormat(tag).format(AT));
    expect(fmt.date(AT)).not.toBe(formatterFor("en").date(AT));
  });

  it("uses Intl for relative times", () => {
    const now = AT.getTime();
    const rtf = new Intl.RelativeTimeFormat(tag, { numeric: "auto", style: "narrow" });
    expect(fmt.relative(now - 5 * 60_000, now)).toBe(rtf.format(-5, "minute"));
    expect(fmt.relative(now - 10_000, now)).toBe(rtf.format(0, "second"));
  });
});
