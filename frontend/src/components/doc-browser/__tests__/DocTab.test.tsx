import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocTab, type DocTabData } from "../DocTab";

const tab: DocTabData = { id: "doc1", filename: "report.docx", format: "docx", included: true };

function renderTab(overrides: Partial<Parameters<typeof DocTab>[0]> = {}) {
  const props = {
    tab,
    isActive: false,
    onSelect: vi.fn(),
    onClose: vi.fn(),
    onToggleInclude: vi.fn(),
    ...overrides,
  };
  return { ...render(<DocTab {...props} />), props };
}

describe("DocTab", () => {
  it("renders filename and format badge", () => {
    renderTab();
    expect(screen.getByText("report.docx")).toBeInTheDocument();
    expect(screen.getByText("docx")).toBeInTheDocument();
  });

  it("applies active styling when isActive", () => {
    const { container } = renderTab({ isActive: true });
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("border-primary");
  });

  it("calls onSelect with tab id when clicked", async () => {
    const { props } = renderTab();
    await userEvent.click(screen.getByText("report.docx"));
    expect(props.onSelect).toHaveBeenCalledWith("doc1");
  });

  it("calls onClose with tab id when × button clicked", async () => {
    const { props } = renderTab({ isActive: true });
    await userEvent.click(screen.getByLabelText(/close report\.docx/i));
    expect(props.onClose).toHaveBeenCalledWith("doc1");
  });

  it("close click does not also fire onSelect", async () => {
    const { props } = renderTab({ isActive: true });
    await userEvent.click(screen.getByLabelText(/close report\.docx/i));
    expect(props.onSelect).not.toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalledWith("doc1");
  });

  it("renders included checkbox as checked when tab.included=true", () => {
    renderTab();
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toHaveAttribute("aria-checked", "true");
  });

  it("renders included checkbox as unchecked when tab.included=false", () => {
    renderTab({ tab: { ...tab, included: false } });
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toHaveAttribute("aria-checked", "false");
  });

  it("clicking the include checkbox calls onToggleInclude and not onSelect", async () => {
    const { props } = renderTab();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(props.onToggleInclude).toHaveBeenCalledWith("doc1");
    expect(props.onSelect).not.toHaveBeenCalled();
  });
});
