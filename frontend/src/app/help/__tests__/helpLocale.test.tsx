// /help with translations: the server renders the source and each translation the
// deployment can show; the client picks by the person's language. `xx` is the
// made-up fixture locale (sprint MULTI-LANG ruling 4).
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleVariant } from "@/components/help/LocaleVariant";
import { LocaleProvider } from "@/i18n";
import { pickVariant } from "@/i18n/resolve";

const meta = (title: string, summary: string, anchor: string) => ({
  slug: "folders",
  title,
  summary,
  audience: "user" as const,
  order: 1,
  requires: [],
  layer: "platform" as const,
  sections: [{ level: 2, title: anchor, anchor }],
});
const SOURCE = { meta: meta("Folders", "About folders.", "make-it-searchable"), body: "## Make it searchable\n\nSource body.\n" };
const XX = { locale: "xx", meta: meta("Xx folders", "Xx about folders.", "xx-searchable"), body: "## Xx searchable\n\nXx body.\n" };

const guide = vi.hoisted(() => ({ translations: [] as unknown[], cards: {} as Record<string, unknown> }));
vi.mock("@/lib/userGuide", () => ({
  readManifest: () => ({ tags: [], pages: [SOURCE.meta], locales: { xx: ["folders"] } }),
  readPage: (slug: string) => (slug === "folders" ? SOURCE : null),
  pageTranslations: () => guide.translations,
  translatedCards: () => guide.cards,
}));
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("404"); }, usePathname: () => "/help" }));

import HelpPage from "../[slug]/page";
import HelpIndex from "../page";

async function page(locale: string) {
  const el = (await HelpPage({ params: Promise.resolve({ slug: "folders" }) })) as ReactElement;
  return { el, ...render(<LocaleProvider locale={locale} defaultLocale="en">{el}</LocaleProvider>) };
}

beforeEach(() => {
  guide.translations = [];
  guide.cards = {};
});

describe("/help/<slug> with a translation", () => {
  beforeEach(() => {
    guide.translations = [XX];
  });

  it("shows the xx page when xx is chosen", async () => {
    await page("xx");
    expect(screen.getByRole("heading", { level: 1, name: "Xx folders" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Xx searchable" })).toHaveAttribute("id", "xx-searchable");
    expect(screen.queryByText("Source body.")).toBeNull();
  });

  it("shows the source page when it isn't, and only one variant is in the DOM", async () => {
    const { container } = await page("en");
    expect(screen.getByRole("heading", { level: 1, name: "Folders" })).toBeInTheDocument();
    expect(screen.getByText("Source body.")).toBeInTheDocument();
    expect(screen.queryByText("Xx body.")).toBeNull();
    expect(container.querySelectorAll("article")).toHaveLength(1);
  });

  it("falls back to the source for a language with no translation", async () => {
    await page("da");
    expect(screen.getByRole("heading", { level: 1, name: "Folders" })).toBeInTheDocument();
  });
});

describe("single-language deployment (no translations shown)", () => {
  it("renders the article itself, with no LocaleVariant around it", async () => {
    const { el } = await page("en");
    expect(el.type).not.toBe(LocaleVariant);
    expect((el.props as { meta: unknown }).meta).toBe(SOURCE.meta);
    expect(screen.getByRole("heading", { level: 1, name: "Folders" })).toBeInTheDocument();
  });
});

describe("/help index cards", () => {
  it("use the translation's title and summary when there is one", () => {
    guide.cards = { folders: { xx: { title: "Xx folders", summary: "Xx about folders." } } };
    render(<LocaleProvider locale="xx" defaultLocale="en">{HelpIndex()}</LocaleProvider>);
    expect(screen.getByText("Xx folders")).toBeInTheDocument();
    expect(screen.getByText("Xx about folders.")).toBeInTheDocument();
  });

  it("show the source without one, or in another language", () => {
    guide.cards = { folders: { xx: { title: "Xx folders", summary: "Xx about folders." } } };
    render(<LocaleProvider locale="en" defaultLocale="en">{HelpIndex()}</LocaleProvider>);
    expect(screen.getByText("Folders")).toBeInTheDocument();
    expect(screen.queryByText("Xx folders")).toBeNull();
  });
});

describe("pickVariant (the <locale>/ sibling-folder shape)", () => {
  const v = { xx: "X", pt: "P" };
  it("walks the chain and stops at the source", () => {
    expect(pickVariant(v, "xx", "en")).toBe("X");
    expect(pickVariant(v, "pt-BR", "en")).toBe("P");
    expect(pickVariant(v, "en", "xx")).toBeUndefined(); // chose English on an xx-default deployment
    expect(pickVariant(v, "da", "xx")).toBe("X"); // no da: the deployment default's translation
    expect(pickVariant(undefined, "xx", "en")).toBeUndefined();
    expect(pickVariant({}, "constructor", "en")).toBeUndefined(); // never an inherited property
  });
});
