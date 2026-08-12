import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { WorkbenchHome } from "../WorkbenchHome";
import type { A2uiArtifactEntry } from "@/providers/SurfaceRegistry";

function artifact(over: Partial<A2uiArtifactEntry>): A2uiArtifactEntry {
  return {
    surfaceId: "ppa_clauses:doc-A",
    kind: "clauses",
    title: "Clauses",
    description: "acme.pdf · 12 clauses",
    createdAt: Date.now(),
    ...over,
  };
}

describe("WorkbenchHome (6.11) — results navigation index", () => {
  it("indexes artifacts and opens a result on click", () => {
    const onOpen = vi.fn();
    render(
      <WorkbenchHome
        artifacts={[artifact({ surfaceId: "web_sources", kind: "sources", title: "Sources" })]}
        onOpen={onOpen}
      />,
    );
    fireEvent.click(screen.getByText("Sources"));
    expect(onOpen).toHaveBeenCalledWith("web_sources");
  });

  it("lists newest result first", () => {
    render(
      <WorkbenchHome
        artifacts={[
          artifact({ surfaceId: "a", title: "First", createdAt: 1 }),
          artifact({ surfaceId: "b", title: "Second", createdAt: 2 }),
        ]}
        onOpen={() => {}}
      />,
    );
    const rows = screen.getAllByText(/First|Second/);
    expect(rows[0]).toHaveTextContent("Second");
  });

  it("shows a Document jump row that focuses the Document tab", () => {
    const onOpenDocument = vi.fn();
    render(
      <WorkbenchHome
        artifacts={[artifact({})]}
        onOpen={() => {}}
        openDocId="doc-A"
        onOpenDocument={onOpenDocument}
      />,
    );
    fireEvent.click(screen.getByTestId("home-document-row"));
    expect(onOpenDocument).toHaveBeenCalled();
  });

  it("shows an empty hint when there is nothing to navigate", () => {
    render(<WorkbenchHome artifacts={[]} onOpen={() => {}} />);
    expect(screen.getByTestId("home-empty")).toBeInTheDocument();
  });
});
