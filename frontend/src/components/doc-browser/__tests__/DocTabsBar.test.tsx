import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocTabData } from "../DocTab";
import { DocTabsBar } from "../DocTabsBar";

const tabs: DocTabData[] = [
  { id: "d1", filename: "alpha.docx", format: "docx", included: true },
  { id: "d2", filename: "beta.pdf", format: "pdf", included: true },
  { id: "d3", filename: "gamma.xlsx", format: "xlsx", included: true },
];

function renderBar(overrides: Partial<Parameters<typeof DocTabsBar>[0]> = {}) {
  const props = {
    tabs,
    activeTabId: "d1",
    showBrowser: true,
    onSelect: vi.fn(),
    onClose: vi.fn(),
    onToggleInclude: vi.fn(),
    onToggleBrowser: vi.fn(),
    ...overrides,
  };
  return { ...render(<DocTabsBar {...props} />), props };
}

describe("DocTabsBar", () => {
  it("renders all three tab filenames", () => {
    renderBar();
    expect(screen.getByText("alpha.docx")).toBeInTheDocument();
    expect(screen.getByText("beta.pdf")).toBeInTheDocument();
    expect(screen.getByText("gamma.xlsx")).toBeInTheDocument();
  });

  it("shows 'No open documents' when tabs is empty", () => {
    renderBar({ tabs: [] });
    expect(screen.getByText(/no open documents/i)).toBeInTheDocument();
  });

  it("clicking a tab calls onSelect with its id", async () => {
    const { props } = renderBar();
    await userEvent.click(screen.getByText("beta.pdf"));
    expect(props.onSelect).toHaveBeenCalledWith("d2");
  });

  it("calls onToggleBrowser when toggle button clicked", async () => {
    const { props } = renderBar();
    await userEvent.click(screen.getByLabelText(/sidebar/i));
    expect(props.onToggleBrowser).toHaveBeenCalled();
  });

  it("does not render an upload button", () => {
    renderBar();
    expect(screen.queryByLabelText(/upload document/i)).not.toBeInTheDocument();
  });
});
