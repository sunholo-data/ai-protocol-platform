import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { InContextBadge } from "../InContextBadge";
import type { DocTabData } from "@/components/doc-browser/DocTab";

function makeTab(id: string, filename: string): DocTabData {
  return {
    id,
    filename,
    format: "pdf",
    included: true,
  };
}

describe("InContextBadge", () => {
  it("renders nothing when no docs are included", () => {
    const { container } = render(
      <InContextBadge openTabs={[]} includedDocIds={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders 'Will process: <filename>' for a single included doc", () => {
    const tabs = [makeTab("doc-1", "contract-a.pdf"), makeTab("doc-2", "contract-b.pdf")];
    render(<InContextBadge openTabs={tabs} includedDocIds={["doc-1"]} />);
    expect(screen.getByText("Will process: contract-a.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/contract-b/)).not.toBeInTheDocument();
  });

  it("renders 'Will process N documents on next turn' for multi-doc", () => {
    const tabs = [
      makeTab("a", "a.pdf"),
      makeTab("b", "b.pdf"),
      makeTab("c", "c.pdf"),
    ];
    render(<InContextBadge openTabs={tabs} includedDocIds={["a", "b", "c"]} />);
    expect(
      screen.getByText("Will process 3 documents on next turn"),
    ).toBeInTheDocument();
  });
});
