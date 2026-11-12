import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SidebarResizeHandle } from "../SidebarResizeHandle";
import { SIDEBAR_DEFAULT, SIDEBAR_MAX, SIDEBAR_MIN, clampSidebarWidth } from "@/hooks/useSidebarWidth";

function setup(width = 300) {
  const onChange = vi.fn();
  render(
    <div>
      <aside data-testid="aside" />
      <SidebarResizeHandle width={width} onChange={onChange} />
    </div>,
  );
  const aside = screen.getByTestId("aside");
  aside.getBoundingClientRect = () => ({ left: 40, right: 340, width: 300, top: 0, bottom: 0, height: 0, x: 40, y: 0, toJSON: () => ({}) });
  return { onChange, handle: screen.getByRole("separator", { name: "Resize sidebar" }) };
}

describe("SidebarResizeHandle", () => {
  it("dragging sets the width from the sidebar's left edge", () => {
    const { onChange, handle } = setup();
    fireEvent.pointerDown(handle, { clientX: 340 });
    fireEvent.pointerMove(document, { clientX: 440 });
    expect(onChange).toHaveBeenLastCalledWith(400);
    fireEvent.pointerUp(document);
    onChange.mockClear();
    fireEvent.pointerMove(document, { clientX: 500 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("is keyboard-operable and resets on double-click", () => {
    const { onChange, handle } = setup(300);
    expect(handle).toHaveAttribute("aria-valuenow", "300");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith(316);
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith(284);
    fireEvent.keyDown(handle, { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith(SIDEBAR_MAX);
    fireEvent.keyDown(handle, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith(SIDEBAR_MIN);
    fireEvent.doubleClick(handle);
    expect(onChange).toHaveBeenLastCalledWith(SIDEBAR_DEFAULT);
  });
});

describe("clampSidebarWidth", () => {
  it("keeps the width in range", () => {
    expect(clampSidebarWidth(10)).toBe(SIDEBAR_MIN);
    expect(clampSidebarWidth(9999)).toBe(SIDEBAR_MAX);
    expect(clampSidebarWidth(300.4)).toBe(300);
    expect(clampSidebarWidth(Number.NaN)).toBe(SIDEBAR_DEFAULT);
  });
});
