import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { SidebarSection } from "../SidebarSection";

describe("SidebarSection", () => {
  // Deterministic in-memory localStorage (jsdom's is flaky across pool modes).
  beforeEach(() => {
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
      clear: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
      key: (i: number) => Object.keys(store)[i] ?? null,
      get length() {
        return Object.keys(store).length;
      },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });
  it("renders title and children inside a <details> element open by default", () => {
    render(
      <SidebarSection title="Sessions">
        <div data-testid="body">body content</div>
      </SidebarSection>,
    );
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByTestId("body")).toBeInTheDocument();
    // <details> defaults open
    const details = screen.getByText("Sessions").closest("details");
    expect(details).toHaveAttribute("open");
  });

  it("respects defaultOpen=false to render collapsed", () => {
    render(
      <SidebarSection title="History" defaultOpen={false}>
        <div data-testid="body">body</div>
      </SidebarSection>,
    );
    const details = screen.getByText("History").closest("details");
    expect(details).not.toHaveAttribute("open");
  });

  it("renders badge slot when provided", () => {
    render(
      <SidebarSection title="Sessions" badge={<span data-testid="badge">3</span>}>
        body
      </SidebarSection>,
    );
    expect(screen.getByTestId("badge")).toBeInTheDocument();
  });

  it("renders action slot when provided", () => {
    render(
      <SidebarSection title="Documents" action={<button data-testid="action">+</button>}>
        body
      </SidebarSection>,
    );
    expect(screen.getByTestId("action")).toBeInTheDocument();
  });

  it("applies bodyClassName override when provided", () => {
    const { container } = render(
      <SidebarSection title="Custom" bodyClassName="px-0 py-0">
        <div data-testid="body">body</div>
      </SidebarSection>,
    );
    const body = container.querySelector("details > div");
    expect(body?.className).toContain("px-0");
    expect(body?.className).toContain("py-0");
  });

  it("persists open/closed state to localStorage when persistId is set", async () => {
    render(
      <SidebarSection title="Library" persistId="library" defaultOpen={true}>
        body
      </SidebarSection>,
    );
    // Starts open; clicking the header collapses it and records the choice.
    await userEvent.click(screen.getByText("Library"));
    expect(screen.getByText("Library").closest("details")).not.toHaveAttribute("open");
    expect(window.localStorage.getItem("aitana.sidebar.section.library")).toBe("0");
  });

  it("restores the remembered state on mount (survives a sidebar reopen)", () => {
    // Simulate a prior collapse persisted before this mount.
    window.localStorage.setItem("aitana.sidebar.section.files", "0");
    render(
      <SidebarSection title="Your files" persistId="files" defaultOpen={true}>
        body
      </SidebarSection>,
    );
    // Even though defaultOpen=true, the remembered "0" wins.
    expect(screen.getByText("Your files").closest("details")).not.toHaveAttribute("open");
  });

  it("does not touch localStorage without a persistId", async () => {
    render(
      <SidebarSection title="Ephemeral" defaultOpen={true}>
        body
      </SidebarSection>,
    );
    await userEvent.click(screen.getByText("Ephemeral"));
    expect(window.localStorage.length).toBe(0);
  });
});
