import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ClausesArtefactTab } from "../ClausesArtefactTab";

let dataRoot: unknown = null;
vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => ({ surface: { dataModel: { get: (p: string) => (p === "/" ? dataRoot : undefined) } } }),
}));

function setData(d: unknown) {
  dataRoot = d;
}

describe("ClausesArtefactTab (6.11)", () => {
  it("renders a table row per clause with value + confidence badge", () => {
    setData({
      docName: "DemoCorp PPA.pdf",
      clauses: [
        { name: "Term (Years)", value: "15 years", confidence: "high" },
        { name: "Price Formula", value: "€ 40.00 / MWh", confidence: "medium" },
      ],
    });
    render(<ClausesArtefactTab surfaceId="ppa_clauses:doc-A" />);
    expect(screen.getByText("DemoCorp PPA.pdf")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Term \(Years\)/ })).toBeInTheDocument();
    expect(screen.getByText("15 years")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows an em dash for an empty value", () => {
    setData({ clauses: [{ name: "Governing Law", value: "", confidence: "" }] });
    render(<ClausesArtefactTab surfaceId="x" />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("notes truncation when present", () => {
    setData({ clauses: [{ name: "A", value: "1" }], truncatedTotal: 27 });
    render(<ClausesArtefactTab surfaceId="x" />);
    expect(screen.getByText(/Showing 1 of 27 clauses/)).toBeInTheDocument();
  });

  it("empty state when no clauses", () => {
    setData({ clauses: [] });
    render(<ClausesArtefactTab surfaceId="x" />);
    expect(screen.getByText(/No clauses were extracted/)).toBeInTheDocument();
  });
});
