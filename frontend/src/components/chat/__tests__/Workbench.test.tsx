// Tests for Workbench — the persistent tabbed pane that replaces the
// single-slot conditional ladder (G31 — template-chat-surface-defaults.md).
//
// The critical contract these tests pin: inactive tabs stay mounted via
// `hidden` className. That's what preserves MCP App iframe handshake
// state across tab switches. Verifying this prevents the regression
// where a future refactor "cleans up" by unmounting inactive tabs.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Workbench, type WorkbenchTab, useTabBadges } from "../Workbench";

function tab(id: string, content: React.ReactNode, extras: Partial<WorkbenchTab> = {}): WorkbenchTab {
  return { id, label: id, content, ...extras };
}

describe("Workbench", () => {
  it("renders one tab button per tab in order", () => {
    render(
      <Workbench
        tabs={[tab("a", "A body"), tab("b", "B body"), tab("c", "C body")]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    const tablist = screen.getByRole("tablist");
    const buttons = within(tablist).getAllByRole("tab");
    expect(buttons.map((b) => b.textContent)).toEqual(["a", "b", "c"]);
  });

  it("sets aria-selected only on the active tab", () => {
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B")]}
        activeTabId="b"
        onActiveTabChange={() => {}}
      />,
    );
    const tablist = screen.getByRole("tablist");
    const buttons = within(tablist).getAllByRole("tab");
    expect(buttons[0].getAttribute("aria-selected")).toBe("false");
    expect(buttons[1].getAttribute("aria-selected")).toBe("true");
  });

  it("keeps ALL tab contents mounted (G31 contract) — inactive ones hidden by className, not unmounted", () => {
    render(
      <Workbench
        tabs={[
          tab("a", <div data-testid="a-body">A body</div>),
          tab("b", <div data-testid="b-body">B body</div>),
          tab("c", <div data-testid="c-body">C body</div>),
        ]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    // CRITICAL: all three tab bodies exist in the DOM even though only
    // 'a' is active. This is what makes MCP App iframe state survive
    // tab switches.
    expect(screen.getByTestId("a-body")).toBeInTheDocument();
    expect(screen.getByTestId("b-body")).toBeInTheDocument();
    expect(screen.getByTestId("c-body")).toBeInTheDocument();

    // Inactive tabpanels carry the `hidden` class.
    const panels = screen
      .getAllByRole("tabpanel", { hidden: true })
      .concat(screen.getAllByRole("tabpanel"));
    const aPanel = panels.find((p) => p.id === "workbench-panel-a") as HTMLElement;
    const bPanel = panels.find((p) => p.id === "workbench-panel-b") as HTMLElement;
    expect(aPanel.className).not.toContain("hidden");
    expect(bPanel.className).toContain("hidden");
  });

  it("preserves ref identity across tab switches (proxy for iframe state preservation)", () => {
    // We render a child with a stable React ref. If the Workbench were
    // unmounting inactive tabs, the ref would change between renders
    // when we toggle back. If hidden-only, the same node persists.
    let aNode: HTMLElement | null = null;
    let aNodeAfterToggle: HTMLElement | null = null;

    function StatefulHarness() {
      const [active, setActive] = useState("a");
      return (
        <div>
          <button type="button" onClick={() => setActive(active === "a" ? "b" : "a")}>
            toggle
          </button>
          <Workbench
            tabs={[
              tab("a", <div ref={(el) => { if (el && !aNode) aNode = el; aNodeAfterToggle = el; }} data-testid="a-body" />),
              tab("b", <div data-testid="b-body" />),
            ]}
            activeTabId={active}
            onActiveTabChange={setActive}
          />
        </div>
      );
    }

    render(<StatefulHarness />);
    const firstRef = aNode;
    fireEvent.click(screen.getByText("toggle")); // active="b"
    fireEvent.click(screen.getByText("toggle")); // active="a"
    expect(aNodeAfterToggle).not.toBeNull();
    expect(aNodeAfterToggle).toBe(firstRef);
  });

  it("calls onActiveTabChange when a tab button is clicked", () => {
    const onActiveTabChange = vi.fn();
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B")]}
        activeTabId="a"
        onActiveTabChange={onActiveTabChange}
      />,
    );
    fireEvent.click(screen.getAllByRole("tab")[1]);
    expect(onActiveTabChange).toHaveBeenCalledWith("b");
  });

  it("does NOT call onActiveTabChange when a disabled tab is clicked", () => {
    const onActiveTabChange = vi.fn();
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { disabled: true })]}
        activeTabId="a"
        onActiveTabChange={onActiveTabChange}
      />,
    );
    const disabledTab = screen.getAllByRole("tab")[1];
    expect(disabledTab).toBeDisabled();
    fireEvent.click(disabledTab);
    expect(onActiveTabChange).not.toHaveBeenCalled();
  });

  // v6.4.0 INTERNAL-SHELL M2 — badge halo animation, width default,
  // empty-body fallback, fade-in tab activation.

  it("badged inactive tabs include an animate-ping halo + solid dot pair", () => {
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { badged: true })]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    const badge = screen.getByLabelText("new content");
    expect(badge).toBeInTheDocument();
    // halo (ping) span + solid dot span as children
    const innerSpans = badge.querySelectorAll("span");
    expect(innerSpans.length).toBe(2);
    expect(innerSpans[0].className).toContain("animate-ping");
    expect(innerSpans[1].className).toContain("rounded-full");
  });

  it("active tab underline carries the animated zoom-in fade-in classes", () => {
    const { container } = render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B")]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    // The active underline is the only span with `bg-primary` in the tab's
    // wrapper. It sits on the WRAPPER (a sibling of the tab button), not inside
    // the button — so that it spans the label AND the close `×`
    // (CLOSABLE-ARTEFACT-TABS); a `×` hanging off the end of the underline
    // reads as a detached control.
    const wrapper = container
      .querySelector("[role='tab'][aria-selected='true']")
      ?.closest("div.group");
    const underline = wrapper?.querySelector("span.bg-primary");
    expect(underline).toBeTruthy();
    expect(underline?.className).toContain("animate-in");
    expect(underline?.className).toContain("fade-in");
    expect(underline?.className).toContain("zoom-in-x-50");
  });

  it("active tab panel renders with animate-in fade-in classes", () => {
    const { container } = render(
      <Workbench
        tabs={[tab("a", "A body"), tab("b", "B body")]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    const activePanel = container.querySelector("[role='tabpanel'][aria-hidden='false']");
    expect(activePanel?.className).toContain("animate-in");
    expect(activePanel?.className).toContain("fade-in");
  });

  it("applies default 4-breakpoint width scale when no className is provided", () => {
    const { container } = render(
      <Workbench
        tabs={[tab("a", "A")]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    const outer = container.firstChild as HTMLElement;
    expect(outer.className).toContain("md:w-[520px]");
    expect(outer.className).toContain("xl:w-[640px]");
    expect(outer.className).toContain("2xl:w-[760px]");
  });

  it("explicit className wins over the default width scale", () => {
    const { container } = render(
      <Workbench
        tabs={[tab("a", "A")]}
        activeTabId="a"
        onActiveTabChange={() => {}}
        className="w-96"
      />,
    );
    const outer = container.firstChild as HTMLElement;
    expect(outer.className).toContain("w-96");
    // Default scale should NOT be applied when an explicit className is passed.
    expect(outer.className).not.toContain("md:w-[520px]");
  });

  it("renders <EmptyTab> when tab.content is null and emptyBody is set", () => {
    render(
      <Workbench
        tabs={[
          tab("a", null, { label: "Invoice", emptyBody: "Drop an invoice in the sidebar." }),
        ]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    expect(screen.getByText("Drop an invoice in the sidebar.")).toBeInTheDocument();
    // EmptyTab uses tab.label as its title.
    expect(screen.getByRole("heading", { name: "Invoice" })).toBeInTheDocument();
  });

  it("renders nothing in the tab body when content is null and no emptyBody is set", () => {
    const { container } = render(
      <Workbench
        tabs={[tab("a", null)]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    const panel = container.querySelector("[role='tabpanel']");
    expect(panel?.textContent).toBe("");
  });

  it("shows a badge dot on inactive badged tabs and hides it once active", () => {
    const { rerender } = render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { badged: true })]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    expect(screen.queryByLabelText("new content")).toBeInTheDocument();

    // Activate the badged tab — the dot should disappear (the parent is
    // expected to clear the flag too; the Workbench's role is just to
    // hide the visual indicator when active).
    rerender(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { badged: true })]}
        activeTabId="b"
        onActiveTabChange={() => {}}
      />,
    );
    expect(screen.queryByLabelText("new content")).not.toBeInTheDocument();
  });

  it("arrow-right moves activation to the next non-disabled tab", () => {
    const onActiveTabChange = vi.fn();
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { disabled: true }), tab("c", "C")]}
        activeTabId="a"
        onActiveTabChange={onActiveTabChange}
      />,
    );
    const tablist = screen.getByRole("tablist");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    // Skips disabled 'b', lands on 'c'.
    expect(onActiveTabChange).toHaveBeenCalledWith("c");
  });

  it("arrow-left wraps around to the last non-disabled tab", () => {
    const onActiveTabChange = vi.fn();
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B"), tab("c", "C")]}
        activeTabId="a"
        onActiveTabChange={onActiveTabChange}
      />,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onActiveTabChange).toHaveBeenCalledWith("c");
  });
});

describe("useTabBadges", () => {
  function Harness() {
    const { mark, clearOnActivate, isBadged } = useTabBadges();
    return (
      <div>
        <span data-testid="a-badged">{String(isBadged("a"))}</span>
        <span data-testid="b-badged">{String(isBadged("b"))}</span>
        <button type="button" onClick={() => mark("a")}>mark-a</button>
        <button type="button" onClick={() => mark("b")}>mark-b</button>
        <button type="button" onClick={() => clearOnActivate("a")}>activate-a</button>
      </div>
    );
  }

  it("isBadged returns true after mark and false after clearOnActivate", () => {
    render(<Harness />);
    expect(screen.getByTestId("a-badged").textContent).toBe("false");

    fireEvent.click(screen.getByText("mark-a"));
    expect(screen.getByTestId("a-badged").textContent).toBe("true");

    fireEvent.click(screen.getByText("activate-a"));
    expect(screen.getByTestId("a-badged").textContent).toBe("false");
  });

  it("clearOnActivate is a no-op when the id was never marked (no extra render)", () => {
    render(<Harness />);
    // Should not throw and should not flip 'b' to true:
    fireEvent.click(screen.getByText("activate-a"));
    expect(screen.getByTestId("a-badged").textContent).toBe("false");
    expect(screen.getByTestId("b-badged").textContent).toBe("false");
  });
});

// Closable tabs (CLOSABLE-ARTEFACT-TABS). The Workbench renders the `×` and
// nothing more — it does NOT confirm. Confirmation belongs to whoever knows
// what the tab cost (ChatShell); see ChatShell.closeArtefactTab.test.tsx.
describe("Workbench — closable tabs", () => {
  it("renders a × ONLY for tabs that pass onClose (structural tabs stay fixed)", () => {
    const onClose = vi.fn();
    render(
      <Workbench
        tabs={[
          tab("Workspace", "home"),
          tab("DE_LU prices", "series", { onClose }),
          tab("Document", "doc"),
          tab("Activity", "activity"),
        ]}
        activeTabId="Workspace"
        onActiveTabChange={() => {}}
      />,
    );
    // The result tab is closable...
    expect(screen.getByLabelText("Close DE_LU prices")).toBeTruthy();
    // ...and the three structural tabs are not.
    expect(screen.queryByLabelText("Close Workspace")).toBeNull();
    expect(screen.queryByLabelText("Close Document")).toBeNull();
    expect(screen.queryByLabelText("Close Activity")).toBeNull();
    // Exactly one close affordance in the whole strip.
    expect(screen.getAllByRole("button", { name: /^Close / })).toHaveLength(1);
  });

  it("the × is a real keyboard-focusable button labelled by the FRIENDLY tab name", () => {
    render(
      <Workbench
        tabs={[tab("entsoe_prices:dk1", "x", { label: "DK1 prices", onClose: vi.fn() })]}
        activeTabId="entsoe_prices:dk1"
        onActiveTabChange={() => {}}
      />,
    );
    // CLAUDE.md #9 — the accessible name is the friendly title, never the
    // surfaceId that happens to be the tab's `id`.
    const close = screen.getByRole("button", { name: "Close DK1 prices" });
    expect(close.tagName).toBe("BUTTON");
    expect(close.textContent).not.toContain("entsoe_prices");
    close.focus();
    expect(document.activeElement).toBe(close);
  });

  it("clicking the × fires onClose WITHOUT selecting the tab", () => {
    const onClose = vi.fn();
    const onActiveTabChange = vi.fn();
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { onClose })]}
        activeTabId="a"
        onActiveTabChange={onActiveTabChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Close b"));
    expect(onClose).toHaveBeenCalledTimes(1);
    // The × must not bubble into a tab-select — closing an inactive tab
    // shouldn't first navigate to it.
    expect(onActiveTabChange).not.toHaveBeenCalled();
  });

  it("does not offer a × on a disabled tab", () => {
    render(
      <Workbench
        tabs={[tab("a", "A"), tab("b", "B", { onClose: vi.fn(), disabled: true })]}
        activeTabId="a"
        onActiveTabChange={() => {}}
      />,
    );
    expect(screen.queryByLabelText("Close b")).toBeNull();
  });
});

// vi needs to be imported via globalImports vitest config; declare it
// here to satisfy tsc when vitest globals are NOT on for this file.
import { vi } from "vitest";
