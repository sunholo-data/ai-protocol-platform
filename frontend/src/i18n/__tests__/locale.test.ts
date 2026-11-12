import { describe, expect, it } from "vitest";

import { localeChain, localeConfig, normaliseLocale, parseLocaleList } from "../locale";
import { localizedField, pickLocale, resolveLocalized } from "../resolve";

describe("locale tags", () => {
  it("normalises case and separators", () => {
    expect(normaliseLocale("pt_br")).toBe("pt-BR");
    expect(normaliseLocale(" DA ")).toBe("da");
    expect(normaliseLocale("es-419")).toBe("es-419");
  });
  it("refuses what isn't a language[-region] tag", () => {
    for (const bad of ["", "english", "d", "da-DK-x", "../da", null, undefined]) expect(normaliseLocale(bad)).toBeNull();
  });
  it("parses the offered list, deduplicated, defaulting to the source", () => {
    expect(parseLocaleList("da, en,da,bogus")).toEqual(["da", "en"]);
    expect(parseLocaleList("")).toEqual(["en"]);
    expect(parseLocaleList(undefined)).toEqual(["en"]);
  });
  it("a default that isn't offered falls back to the first offered", () => {
    expect(localeConfig("da,en", "en")).toEqual({ locales: ["da", "en"], defaultLocale: "en" });
    expect(localeConfig("da,en", "sv")).toEqual({ locales: ["da", "en"], defaultLocale: "da" });
    expect(localeConfig("da,en", "")).toEqual({ locales: ["da", "en"], defaultLocale: "da" });
  });
});

describe("the resolution chain", () => {
  it("requested → its language → default → its language → source", () => {
    expect(localeChain("pt-BR", "da")).toEqual(["pt-BR", "pt", "da", "en"]);
    expect(localeChain("en", "en")).toEqual(["en"]);
    expect(localeChain("da", "de-AT")).toEqual(["da", "de-AT", "de", "en"]);
  });
});

describe("resolving content", () => {
  const map = { en: "Projects", da: "Projekter", pt: "Projetos" };
  it("a map follows the chain", () => {
    expect(resolveLocalized(map, "da", "en")).toBe("Projekter");
    expect(resolveLocalized(map, "pt-BR", "en")).toBe("Projetos");
    expect(resolveLocalized(map, "sv", "da")).toBe("Projekter");
    expect(resolveLocalized(map, "sv", "fi")).toBe("Projects");
  });
  it("ends at any value, then empty — never a key or an error", () => {
    expect(resolveLocalized({ fr: "Projets" }, "sv", "fi")).toBe("Projets");
    expect(resolveLocalized({}, "sv", "fi")).toBe("");
    expect(resolveLocalized(null, "da")).toBe("");
    expect(resolveLocalized("plain", "da")).toBe("plain");
  });
  it("empty strings don't count as a translation", () => {
    expect(pickLocale({ da: "", en: "Projects" }, "da", "en")).toBe("Projects");
  });

  describe("base + variants", () => {
    const locales = { da: { display_name: "Generel assistent" }, en: { display_name: "General assistant (EN)" } };
    it("uses the person's locale, then its language, then the base", () => {
      expect(localizedField("General Assistant", locales, "display_name", "da")).toBe("Generel assistent");
      expect(localizedField("General Assistant", locales, "display_name", "da-DK")).toBe("Generel assistent");
      expect(localizedField("General Assistant", locales, "display_name", "sv")).toBe("General Assistant");
    });
    it("never walks to the deployment default: English chosen gets the base, not a da variant", () => {
      expect(localizedField("General Assistant", { da: { display_name: "Generel assistent" } }, "display_name", "en")).toBe(
        "General Assistant",
      );
    });
    it("a field the variant lacks falls back to the base", () => {
      expect(localizedField("Hi!", { da: { display_name: "x" } }, "initial_message", "da")).toBe("Hi!");
      expect(localizedField(undefined, undefined, "initial_message", "da")).toBe("");
    });
  });
});
