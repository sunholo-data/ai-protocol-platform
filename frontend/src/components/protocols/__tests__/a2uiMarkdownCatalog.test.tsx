// The markdown catalog, built for real — no SDK mock.
//
// The four suites that render surfaces stub `@a2ui/react/v0_9` to avoid
// rendering, so none of them touches the real builder. This file is the one
// that does: if composing Basic ever stops working (an SDK upgrade renames
// `components`, `createReactComponent` changes shape), it fails HERE rather
// than in a browser.

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { basicCatalog } from "@a2ui/react/v0_9";
import { markdownCatalog, MarkdownText, TextContent } from "../a2uiMarkdownCatalog";

describe("markdownCatalog", () => {
  it("keeps basicCatalog's id, so the wire format is unchanged", () => {
    // A server sending `catalogId: basic_catalog.json` must still match. If
    // this drifts, every surface silently falls back or fails to create.
    expect(markdownCatalog.id).toBe(basicCatalog.id);
  });

  it("carries every component Basic has, no more and no fewer", () => {
    expect([...markdownCatalog.components.keys()].sort()).toEqual(
      [...basicCatalog.components.keys()].sort(),
    );
  });

  it("replaces Text and leaves the others alone", () => {
    expect(markdownCatalog.components.get("Text")).toBe(MarkdownText);
    expect(markdownCatalog.components.get("Card")).toBe(basicCatalog.components.get("Card"));
  });

  it("carries the functions across", () => {
    // Dropping them disables expression bindings, which fail as an unresolved
    // {"path": …} rather than as an error — invisible until someone reads it.
    expect([...markdownCatalog.functions.keys()].sort()).toEqual(
      [...basicCatalog.functions.keys()].sort(),
    );
  });
});

function renderText(props: { text: string; variant?: string }) {
  return render(<TextContent {...props} />);
}

describe("TextContent — the rendering decision", () => {
  it("renders markdown emphasis as markup, not as asterisks", () => {
    const { container } = renderText({ text: "a **bold** word" });
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.textContent).not.toContain("**");
  });

  it("renders a code span as code", () => {
    const { container } = renderText({ text: "run `pytest -q` now" });
    expect(container.querySelector("code")?.textContent).toBe("pytest -q");
    expect(container.textContent).not.toContain("`");
  });

  it("leaves headings PLAIN — they are labels, not prose", () => {
    const { container } = renderText({ text: "▸ what is in the workspace?", variant: "h3" });
    expect(container.querySelector("h3")?.textContent).toBe("▸ what is in the workspace?");
    expect(container.querySelector("p")).toBeNull();
  });

  it("renders an empty string as an empty node rather than collapsing", () => {
    // A tool that returned nothing is a real value and must not drop the row.
    const { container } = renderText({ text: "" });
    expect(container.firstChild).not.toBeNull();
  });

  it("opens links in a new tab with noopener", () => {
    // Peer-controlled text renders here; a surface must not be able to
    // navigate the app it is embedded in.
    const { container } = renderText({ text: "[x](https://example.com)" });
    const a = container.querySelector("a");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toContain("noopener");
  });

  it("shows an image's alt text instead of requesting it, and drops raw HTML", () => {
    // A remote image in peer-controlled text is a request we did not choose
    // to make — and an exfiltration channel.
    const { container } = renderText({
      text: "![chart](https://example.com/x.png) <img src=https://example.com/y.png>",
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("chart");
  });
});

// Exercise the real processor: the backend and stored messages can still send
// the old catalog ID even when the renderer dependency changes its default.
import { MessageProcessor } from "@a2ui/web_core/v0_9";
import { LEGACY_BASIC_CATALOG_ID, withBasicCatalogAliases } from "../basicCatalogAliases";

it("accepts stored v0.9 surfaces with the patched catalog functions", () => {
  const processor = new MessageProcessor(withBasicCatalogAliases(markdownCatalog));
  processor.processMessages([{
    version: "v0.9",
    createSurface: { surfaceId: "legacy-security-check", catalogId: LEGACY_BASIC_CATALOG_ID },
  }]);
  const surface = processor.model.getSurface("legacy-security-check");
  expect(surface?.catalog.id).toBe(LEGACY_BASIC_CATALOG_ID);
  expect(surface?.catalog.functions.get("openUrl")).toBe(basicCatalog.functions.get("openUrl"));
});
