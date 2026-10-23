import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearHelpPageCache, HelpArtefactTab } from "../HelpArtefactTab";
import { openHelpInWorkbench, resetHelpViewer, setHelpOpener } from "@/lib/helpViewer";

// The surface data model carries REFERENCES only (backend `help_pages`).
let dataRoot: unknown = null;
vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => ({ surface: { dataModel: { get: (p: string) => (p === "/" ? dataRoot : undefined) } } }),
}));

const PAGES: Record<string, { slug: string; title: string; summary: string; body: string }> = {
  folders: {
    slug: "folders",
    title: "Folders",
    summary: "Keep documents together.",
    body: [
      "Folders hold documents.",
      "",
      "## Upload a whole folder",
      "",
      "1. Click **Upload folder**. See [skills](skills.md#share-your-skill).",
      "",
      '![The upload dialog.](img/folders/04-upload.png "Pick a folder.")',
    ].join("\n"),
  },
  skills: { slug: "skills", title: "Skills", summary: "", body: "## Share your skill\n\nClick **Share**." },
};

const fetchMock = vi.fn(async (url: string) => {
  const page = PAGES[url.replace("/help-content/", "")];
  return page ? new Response(JSON.stringify(page)) : new Response("{}", { status: 404 });
});

beforeEach(() => {
  clearHelpPageCache();
  resetHelpViewer();
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn() as unknown as Element["scrollTo"];
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HelpArtefactTab", () => {
  it("renders the cited page from the app's own build, screenshots included", async () => {
    dataRoot = { help: [{ slug: "folders", anchor: "upload-a-whole-folder", title: "Folders › Upload a whole folder" }] };
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);

    expect(await screen.findByRole("heading", { name: "Folders" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/help-content/folders");
    expect(screen.getByRole("heading", { name: "Upload a whole folder" })).toHaveAttribute("id", "upload-a-whole-folder");
    expect(screen.getByRole("img", { name: "The upload dialog." })).toHaveAttribute("src", "/help/img/folders/04-upload.png");
    expect(screen.getByText("Upload folder").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "Open full page" })).toHaveAttribute("href", "/help/folders#upload-a-whole-folder");
    // Scrolls its own pane to the section, never the page (scrollIntoView would).
    await waitFor(() => expect(Element.prototype.scrollTo).toHaveBeenCalled());
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("follows links between guide pages inside the tab", async () => {
    dataRoot = { help: [{ slug: "folders", anchor: "", title: "Folders" }] };
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);
    fireEvent.click(await screen.findByRole("link", { name: "skills" }));
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeInTheDocument();
  });

  it("switches between matching sections", async () => {
    dataRoot = {
      help: [
        { slug: "folders", anchor: "upload-a-whole-folder", title: "Folders › Upload a whole folder" },
        { slug: "skills", anchor: "share-your-skill", title: "Skills › Share your skill" },
      ],
    };
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);
    await screen.findByRole("heading", { name: "Folders" });
    fireEvent.click(screen.getByRole("button", { name: "Share your skill" }));
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Share your skill" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens a help link clicked in chat", async () => {
    dataRoot = { help: [{ slug: "folders", anchor: "", title: "Folders" }] };
    setHelpOpener(() => {});
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);
    await screen.findByRole("heading", { name: "Folders" });
    act(() => {
      expect(openHelpInWorkbench({ slug: "skills", anchor: "share-your-skill" })).toBe(true);
    });
    expect(await screen.findByRole("heading", { name: "Skills" })).toBeInTheDocument();
  });

  it("says so, with a way out, when a page can't load (never silent)", async () => {
    dataRoot = { help: [{ slug: "missing", anchor: "", title: "Missing" }] };
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this help page");
    expect(screen.getByRole("link", { name: "Open it in a new tab" })).toHaveAttribute("href", "/help/missing");
  });

  it("ignores references that aren't guide slugs", () => {
    dataRoot = { help: [{ slug: "../admin", anchor: "", title: "x" }] };
    render(<HelpArtefactTab surfaceId="knowledge_sources" />);
    expect(screen.getByText(/no help pages were found/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
