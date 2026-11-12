import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageSwitch } from "@/components/settings/LanguageSwitch";
import { CatalogProvider, LocaleProvider, translate, useLocale, useT, type LocaleConfig } from "..";
import { FIXTURE_CATALOG, XX } from "../__fixtures__/catalog";
import { GENERATED_CATALOG, mergedMessages, uncoveredLocales } from "../messages";
import { checkParity, placeholders } from "../parity";
import { USER_LOCALE_KEY, UserLocaleProvider, setDefaultLocaleResolver, useUserLocale } from "../userLocale";

function Title() {
  const t = useT("SettingsPage");
  return <h1>{t("title")}</h1>;
}
function Meta() {
  const t = useT("McpTokensSection");
  return <span data-testid="meta">{t("tokenMeta", { expires: "1 Dec", lastUsed: "never" })}</span>;
}
function Which() {
  return <span data-testid="locale">{useLocale()}</span>;
}

const TWO: LocaleConfig = { locales: ["en", "xx"], defaultLocale: "en" };

describe("the source catalog (CI parity)", () => {
  it("every non-source locale is a subset of the source with matching placeholders", () => {
    const { errors, missing } = checkParity(GENERATED_CATALOG);
    expect(errors).toEqual([]);
    for (const [locale, keys] of Object.entries(missing)) {
      const msg = `${locale}: ${keys.length} message(s) not translated yet (shown in the source language)`;
      if (process.env.I18N_STRICT === "1") throw new Error(`${msg}: ${keys.join(", ")}`);
      console.warn(msg);
    }
  });
  it("every source message formats", () => {
    for (const [ns, table] of Object.entries(GENERATED_CATALOG.en)) {
      const t = translate(ns as never);
      for (const key of Object.keys(table)) {
        const values = Object.fromEntries(
          placeholders(table[key]).map((p) => (p.startsWith("<") ? [p.slice(1, -1), (c: unknown) => c] : [p, "x"])),
        );
        expect(() => t.rich(key as never, values as never)).not.toThrow();
      }
    }
  });
});

describe("parity rules", () => {
  it("flags unknown keys and placeholder drift; reports gaps", () => {
    const { errors, missing } = checkParity({
      en: { A: { hi: "Hi {name}", bye: "Bye" } },
      xx: { A: { hi: "Xi {nom}", extra: "?" }, B: { k: "v" } },
    });
    expect(errors).toEqual([
      "xx: A.hi uses {nom} but en uses {name}",
      "xx: A.extra is not in the en catalog",
      "xx: namespace B is not in the en catalog",
    ]);
    expect(missing).toEqual({ xx: ["A.bye"] });
  });
  it("a configured locale with no catalog is caught", () => {
    // Only the source locale, NOT GENERATED_CATALOG: that includes every fork
    // overlay, so a fork that ships `da` (the design's own worked example)
    // would make this assertion fail through no fault of its own.
    const sourceOnly = { en: GENERATED_CATALOG.en };
    expect(uncoveredLocales({ locales: ["en", "da"], defaultLocale: "en" }, sourceOnly)).toEqual(["da"]);
    expect(uncoveredLocales({ locales: ["en", "pt-BR"], defaultLocale: "en" }, { ...GENERATED_CATALOG, pt: {} })).toEqual([]);
  });
});

describe("useT", () => {
  it("with no provider renders the source locale", () => {
    render(<Title />);
    expect(screen.getByRole("heading").textContent).toBe("Settings");
  });
  it("a provider switches language; a missing key still formats from the source", () => {
    render(
      <CatalogProvider catalog={FIXTURE_CATALOG}>
        <LocaleProvider locale="xx" defaultLocale="en">
          <Title />
          <Meta />
        </LocaleProvider>
      </CatalogProvider>,
    );
    expect(screen.getByRole("heading").textContent).toBe("Xettings");
    expect(screen.getByTestId("meta").textContent).toBe("· xexpires 1 Dec · xlast never");
  });
  it("nested providers override a subtree", () => {
    render(
      <LocaleProvider locale="xx">
        <Which />
        <LocaleProvider locale="en">
          <Which />
        </LocaleProvider>
      </LocaleProvider>,
    );
    expect(screen.getAllByTestId("locale").map((n) => n.textContent)).toEqual(["xx", "en"]);
  });
  it("merging keeps every source key", () => {
    const m = mergedMessages(FIXTURE_CATALOG, "xx", "en");
    expect(m.SettingsPage.title).toBe(XX.SettingsPage.title);
    expect(m.SettingsPage.memoryTitle).toBe("Memory");
  });
});

// Deterministic in-memory localStorage (jsdom's is flaky across pool modes),
// the same stub ChatShell's tests use.
function memoryStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

describe("UserLocaleProvider + LanguageSwitch", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
    document.documentElement.lang = "en";
  });
  afterEach(() => {
    setDefaultLocaleResolver(null);
    vi.unstubAllGlobals();
  });

  const app = (config = TWO) => (
    <CatalogProvider catalog={FIXTURE_CATALOG}>
      <UserLocaleProvider config={config}>
        <LanguageSwitch />
        <Title />
      </UserLocaleProvider>
    </CatalogProvider>
  );

  it("one locale: no switch, source text, nothing stored is read", () => {
    localStorage.setItem(USER_LOCALE_KEY, "xx");
    render(app({ locales: ["en"], defaultLocale: "en" }));
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.getByRole("heading").textContent).toBe("Settings");
  });

  it("choosing a language re-renders, persists and sets <html lang>", async () => {
    const { unmount } = render(app());
    const xx = screen.getByRole("radio", { name: "xx" });
    await act(async () => xx.click());
    expect(screen.getByRole("heading").textContent).toBe("Xettings");
    expect(localStorage.getItem(USER_LOCALE_KEY)).toBe("xx");
    expect(document.documentElement.lang).toBe("xx");
    expect(xx.getAttribute("aria-checked")).toBe("true");
    unmount();

    render(app()); // a reload
    expect(await screen.findByText("Xettings")).toBeTruthy();
  });

  it("an unknown stored value is ignored", () => {
    localStorage.setItem(USER_LOCALE_KEY, "zz");
    render(app());
    expect(screen.getByRole("heading").textContent).toBe("Settings");
  });

  it("storage that throws degrades to the default", () => {
    const denied = () => {
      throw new Error("denied");
    };
    vi.stubGlobal("localStorage", { getItem: denied, setItem: denied, removeItem: denied, clear: denied });
    render(app());
    expect(screen.getByRole("heading").textContent).toBe("Settings");
    act(() => screen.getByRole("radio", { name: "xx" }).click());
    expect(screen.getByRole("heading").textContent).toBe("Xettings");
  });

  it("a registered resolver sets the default; an explicit choice still wins", async () => {
    setDefaultLocaleResolver(() => "xx");
    const { unmount } = render(app());
    expect(await screen.findByText("Xettings")).toBeTruthy();
    unmount();
    localStorage.setItem(USER_LOCALE_KEY, "en");
    render(app());
    expect(screen.getByRole("heading").textContent).toBe("Settings");
  });

  it("useUserLocale reports what's offered", () => {
    let seen: ReturnType<typeof useUserLocale> | null = null;
    function Probe() {
      seen = useUserLocale();
      return null;
    }
    render(
      <UserLocaleProvider config={TWO}>
        <Probe />
      </UserLocaleProvider>,
    );
    expect(seen!.locales).toEqual(["en", "xx"]);
    expect(seen!.explicit).toBe(false);
  });
});
