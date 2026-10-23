import { describe, expect, it } from "vitest";

import { imageUrl, linkUrl, parsePage, slugify, visiblePages, withAppName, type GuidePageMeta } from "../userGuideParse";

const PAGE = `Intro with **{{appName}}**.

## Make a folder searchable

1. Click **Make searchable**.

> [!NOTE]
> Only where knowledge bases are on.

![The menu](img/folders/12.png "Turning search on.")

### Indexing

![One](img/folders/13.png "A.")
![Two](img/folders/14.png "B.")

More text after the pair.

## Upload from the command line

\`\`\`bash
## not a heading
aiplatform docs upload -r x
\`\`\`
`;

describe("slugify", () => {
  // Same cases as scripts/user-guide/bundle.test.mjs — the two must agree.
  it.each([
    ["When the skill isn't yours to change", "when-the-skill-isnt-yours-to-change"],
    ["Upload a folder — from the CLI", "upload-a-folder--from-the-cli"],
    ["**Bold** `code` heading", "bold-code-heading"],
  ])("%s", (title, anchor) => expect(slugify(title)).toBe(anchor));
});

describe("parsePage", () => {
  const sections = parsePage(PAGE);

  it("splits at ## and ###, never inside a code fence", () => {
    expect(sections.map((s) => s.heading?.anchor ?? null)).toEqual([
      null,
      "make-a-folder-searchable",
      "indexing",
      "upload-from-the-command-line",
    ]);
    const cli = sections[3].segments[0];
    expect(cli.kind === "markdown" && cli.text).toContain("## not a heading");
  });

  it("a section ending in an image puts it beside the steps, served from /help/img", () => {
    const s = sections[1];
    expect(s.aside).toEqual([{ alt: "The menu", src: "/help/img/folders/12.png", caption: "Turning search on." }]);
    expect(s.segments.map((x) => x.kind)).toEqual(["markdown", "alert"]);
    const alert = s.segments[1];
    expect(alert.kind === "alert" && [alert.type, alert.text]).toEqual(["note", "Only where knowledge bases are on."]);
  });

  it("images mid-section stay inline figures; text after them means no aside", () => {
    const s = sections[2];
    expect(s.aside).toEqual([]);
    expect(s.segments.map((x) => x.kind)).toEqual(["figures", "markdown"]);
    const figs = s.segments[0];
    expect(figs.kind === "figures" && figs.figures.map((f) => f.caption)).toEqual(["A.", "B."]);
  });
});

describe("urls and names", () => {
  it("rewrites bundle-relative images and page links, leaves the rest", () => {
    expect(imageUrl("img/a/b.png")).toBe("/help/img/a/b.png");
    expect(imageUrl("https://x/y.png")).toBe("https://x/y.png");
    expect(linkUrl("skills.md#fork")).toBe("/help/skills#fork");
    expect(linkUrl("#which")).toBe("#which");
    expect(linkUrl("https://example.com")).toBe("https://example.com");
  });

  it("substitutes the app name token", () => {
    expect(withAppName("Using {{appName}} and {{ appName }}", "Acme")).toBe("Using Acme and Acme");
  });
});

describe("visiblePages", () => {
  const page = (slug: string, extra: Partial<GuidePageMeta> = {}): GuidePageMeta => ({
    slug,
    title: slug,
    summary: "",
    audience: "user",
    order: 1,
    requires: [],
    layer: "platform",
    sections: [],
    ...extra,
  });
  const pages = [page("a"), page("admin", { audience: "admin" }), page("kb", { requires: ["knowledge_bases"] })];

  it("hides admin pages from non-admins and pages whose feature is off", () => {
    expect(visiblePages(pages, { features: new Set() }).map((p) => p.slug)).toEqual(["a"]);
    expect(visiblePages(pages, { isAdmin: true, features: new Set(["knowledge_bases"]) }).map((p) => p.slug)).toEqual(["a", "admin", "kb"]);
  });
});
