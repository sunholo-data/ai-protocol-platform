import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SourcesArtefactTab } from "../SourcesArtefactTab";

// Drive the tab off a mocked surface data model (what the backend transform
// stashes at "/sources").
let dataRoot: unknown = null;
vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => ({ surface: { dataModel: { get: (p: string) => (p === "/" ? dataRoot : undefined) } } }),
}));

function setSources(sources: unknown) {
  dataRoot = { sources };
}

describe("SourcesArtefactTab (6.11)", () => {
  it("renders each source as a clickable link showing the domain, href = uri", () => {
    setSources([
      { title: "twobirds.com", uri: "https://vertexaisearch.cloud.google.com/redirect/AAA" },
      { title: "pv-magazine.com", uri: "https://vertexaisearch.cloud.google.com/redirect/BBB" },
    ]);
    render(<SourcesArtefactTab surfaceId="web_sources" />);

    const first = screen.getByRole("link", { name: /twobirds\.com/ });
    expect(first).toHaveAttribute("href", "https://vertexaisearch.cloud.google.com/redirect/AAA");
    expect(first).toHaveAttribute("target", "_blank");
    expect(first).toHaveAttribute("rel", expect.stringContaining("noopener"));
    // Header count.
    expect(screen.getByText(/2 sources/i)).toBeInTheDocument();
    // The ugly redirect URL is NOT shown as visible text (domain is the label).
    expect(screen.queryByText(/vertexaisearch/)).toBeNull();
  });

  it("falls back to the hostname when no title is given", () => {
    setSources([{ uri: "https://www.reel.energy/article/123" }]);
    render(<SourcesArtefactTab surfaceId="web_sources" />);
    expect(screen.getByRole("link", { name: /reel\.energy/ })).toBeInTheDocument();
  });

  it("shows an empty notice when there are no sources", () => {
    setSources([]);
    render(<SourcesArtefactTab surfaceId="web_sources" />);
    expect(screen.getByText(/no sources were returned/i)).toBeInTheDocument();
  });

  it("renders a gs:// (kind=gcs) source as a doc button that opens via onOpenSource", async () => {
    setSources([
      {
        title: "Report 2024.pdf",
        uri: "gs://dev-bucket/aitana3/cases/x/Report 2024.pdf",
        kind: "gcs",
        bucket: "dev-bucket",
        object: "aitana3/cases/x/Report 2024.pdf",
        filename: "Report 2024.pdf",
      },
    ]);
    const onOpenSource = vi.fn().mockResolvedValue(undefined);
    render(<SourcesArtefactTab surfaceId="web_sources" onOpenSource={onOpenSource} />);

    // It's a BUTTON, not an external link (no dead gs:// href).
    const btn = screen.getByRole("button", { name: /open report 2024\.pdf in the document tab/i });
    expect(screen.queryByRole("link")).toBeNull();

    fireEvent.click(btn);
    await waitFor(() =>
      expect(onOpenSource).toHaveBeenCalledWith("dev-bucket", "aitana3/cases/x/Report 2024.pdf"),
    );
  });

  it("shows a visible error when opening a gs:// source fails (never-silent)", async () => {
    setSources([
      { uri: "gs://b/o.pdf", kind: "gcs", bucket: "b", object: "o.pdf", filename: "o.pdf" },
    ]);
    const onOpenSource = vi.fn().mockRejectedValue(new Error("403"));
    render(<SourcesArtefactTab surfaceId="web_sources" onOpenSource={onOpenSource} />);

    fireEvent.click(screen.getByRole("button", { name: /open o\.pdf/i }));
    expect(await screen.findByText(/couldn't open this document/i)).toBeInTheDocument();
  });
});
