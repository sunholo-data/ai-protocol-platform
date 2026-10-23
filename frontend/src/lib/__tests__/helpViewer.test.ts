import { beforeEach, describe, expect, it, vi } from "vitest";

import { isAppPath, latestHelpRequest, openHelpInWorkbench, parseHelpHref, resetHelpViewer, setHelpOpener } from "../helpViewer";

beforeEach(() => resetHelpViewer());

describe("helpViewer", () => {
  it("recognises the guide's own links only", () => {
    expect(parseHelpHref("/help/folders#upload-a-whole-folder")).toEqual({ slug: "folders", anchor: "upload-a-whole-folder" });
    expect(parseHelpHref("/help/skills")).toEqual({ slug: "skills", anchor: "" });
    for (const href of ["https://evil.example/help/x", "//evil.example/help/x", "/help/../admin", "/help", "/chat/x"]) {
      expect(parseHelpHref(href)).toBeNull();
    }
  });

  it("treats only same-site paths as app paths", () => {
    expect(isAppPath("/chat/@aitana-platform/web-researcher")).toBe(true);
    expect(isAppPath("//evil.example")).toBe(false);
    expect(isAppPath("/\\evil.example")).toBe(false);
    expect(isAppPath("https://x.example")).toBe(false);
  });

  it("opens in the workbench only when a Help tab exists", () => {
    expect(openHelpInWorkbench({ slug: "folders", anchor: "" })).toBe(false);
    const opener = vi.fn();
    setHelpOpener(opener);
    expect(openHelpInWorkbench({ slug: "folders", anchor: "x" })).toBe(true);
    expect(opener).toHaveBeenCalledWith({ slug: "folders", anchor: "x" });
    expect(latestHelpRequest()).toMatchObject({ slug: "folders", anchor: "x" });
  });
});
