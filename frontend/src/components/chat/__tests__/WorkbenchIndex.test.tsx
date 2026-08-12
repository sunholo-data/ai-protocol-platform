import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { WorkbenchIndex } from "../WorkbenchIndex";
import type { A2uiArtifactEntry } from "@/providers/SurfaceRegistry";

function artifact(over: Partial<A2uiArtifactEntry>): A2uiArtifactEntry {
  return {
    surfaceId: "ppa_clauses:doc-A",
    kind: "clauses",
    title: "Clauses",
    description: "acme.pdf · 12 clauses extracted",
    createdAt: Date.now(),
    ...over,
  };
}

describe("WorkbenchIndex (7.5 M3)", () => {
  const two = [
    artifact({ surfaceId: "ppa_clauses:doc-A", title: "Clauses", description: "acme.pdf · 12 clauses" }),
    artifact({ surfaceId: "ppa_comparison", kind: "comparison", title: "Comparison", description: "3 differences" }),
  ];

  it("renders one row per artifact with title + description", () => {
    render(<WorkbenchIndex artifacts={two} onOpen={() => {}} />);
    expect(screen.getByText("Clauses")).toBeInTheDocument();
    expect(screen.getByText("acme.pdf · 12 clauses")).toBeInTheDocument();
    expect(screen.getByText("Comparison")).toBeInTheDocument();
    expect(screen.getByText("3 differences")).toBeInTheDocument();
    // Header summarises the count.
    expect(screen.getByText(/2 results in this session/)).toBeInTheDocument();
  });

  it("opens an artifact's tab when its row is clicked", () => {
    const onOpen = vi.fn();
    render(<WorkbenchIndex artifacts={two} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("Comparison"));
    expect(onOpen).toHaveBeenCalledWith("ppa_comparison");
  });

  it("falls back to kind when title is missing", () => {
    render(<WorkbenchIndex artifacts={[artifact({ title: undefined, kind: "sources" })]} onOpen={() => {}} />);
    // kind chip + title fallback both derive from kind
    expect(screen.getAllByText("sources").length).toBeGreaterThan(0);
  });
});
