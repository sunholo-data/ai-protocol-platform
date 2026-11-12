// The server-side guide loader with translations (multi-language.md §5), against
// a temporary bundle. `xx` is the made-up fixture locale (sprint MULTI-LANG ruling 4).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { pageTranslations, readLocaleManifest, readManifest, readPage, shownLocales, translatedCards } from "../userGuide";

const meta = (slug: string, title: string, summary: string) => ({
  slug,
  title,
  summary,
  audience: "user",
  order: 1,
  requires: [],
  layer: "platform",
  sections: [],
});

let dir = "";
beforeAll(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "guide-bundle-"));
  const put = (rel: string, text: string) => {
    fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    fs.writeFileSync(path.join(dir, rel), text);
  };
  put(
    "manifest.json",
    JSON.stringify({ tags: [], pages: [meta("folders", "Folders", "About folders."), meta("skills", "Skills", "About skills.")], locales: { xx: ["folders"] } }),
  );
  put("folders.md", "## Make it searchable\n");
  put("skills.md", "## Share\n");
  put("xx/manifest.json", JSON.stringify({ locale: "xx", pages: [meta("folders", "Xx folders", "Xx about.")] }));
  put("xx/folders.md", "## Xx searchable\n");
  // Outside the bundle: must never be reachable by a crafted slug or locale.
  put("../secret.md", "secret");
});

describe("readPage", () => {
  it("reads the source page, or a translation by locale", () => {
    expect(readPage("folders", dir)?.body).toBe("## Make it searchable\n");
    expect(readPage("folders", dir, "en")?.meta.title).toBe("Folders");
    expect(readPage("folders", dir, "xx")).toEqual({ meta: expect.objectContaining({ title: "Xx folders" }), body: "## Xx searchable\n" });
  });

  it("returns null for a page with no translation, so callers fall back to the source", () => {
    expect(readPage("skills", dir, "xx")).toBeNull();
    expect(readPage("folders", dir, "da")).toBeNull();
  });

  it("refuses anything that isn't a plain slug and a locale tag (no path traversal)", () => {
    for (const slug of ["../secret", "..%2Fsecret", "folders/../skills", "", "a b"]) expect(readPage(slug, dir)).toBeNull();
    for (const locale of ["..", "../xx", "xx/..", "XX", "xx_yy", "/etc", "xx/../.."]) expect(readPage("folders", dir, locale)).toBeNull();
    expect(readLocaleManifest("../xx", dir)).toEqual([]);
    expect(readLocaleManifest("en", dir)).toEqual([]);
  });

  it("a bundle without translations reads as before", () => {
    expect(readManifest(path.join(dir, "nowhere"))).toEqual({ tags: [], pages: [] });
    expect(readLocaleManifest("xx", path.join(dir, "nowhere"))).toEqual([]);
  });
});

describe("which translations a deployment shows", () => {
  it("none on a single-language (English-only) deployment, whatever the bundle holds", () => {
    expect(shownLocales(readManifest(dir), ["en"])).toEqual([]);
    expect(pageTranslations("folders", dir, ["en"])).toEqual([]);
    expect(translatedCards(dir, ["en"])).toEqual({});
  });

  it("those an offered locale reaches, region tags reaching their language", () => {
    expect(shownLocales(readManifest(dir), ["en", "xx"])).toEqual(["xx"]);
    expect(shownLocales(readManifest(dir), ["en", "xx-YY"])).toEqual(["xx"]);
    expect(shownLocales(readManifest(dir), ["en", "da"])).toEqual([]);
    expect(pageTranslations("folders", dir, ["en", "xx"]).map((t) => [t.locale, t.meta.title])).toEqual([["xx", "Xx folders"]]);
    expect(pageTranslations("skills", dir, ["en", "xx"])).toEqual([]);
    expect(translatedCards(dir, ["en", "xx"])).toEqual({ folders: { xx: { title: "Xx folders", summary: "Xx about." } } });
  });
});
