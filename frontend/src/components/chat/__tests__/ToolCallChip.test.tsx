import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToolCallChip } from "../ToolCallChip";
import type { ToolCallState } from "@/hooks/useSkillAgent";
import { __resetToolLabelsCache } from "@/hooks/useToolLabels";

// The chip resolves friendly labels from GET /api/tools. Default: the fetch
// fails, so labels come from the static framework map + humanise.
const fetchWithAuth = vi.fn(() => Promise.reject(new Error("no catalog in test")));
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => (fetchWithAuth as (...a: unknown[]) => unknown)(...args),
}));

function chip(overrides: Partial<ToolCallState> = {}): ToolCallState {
  return { id: "tc-1", name: "web_search_agent", status: "running", ...overrides };
}

describe("ToolCallChip", () => {
  beforeEach(() => __resetToolLabelsCache());
  afterEach(() => {
    __resetToolLabelsCache();
    fetchWithAuth.mockImplementation(() => Promise.reject(new Error("no catalog in test")));
  });

  it("shows the friendly label, not the raw tool name (CLAUDE.md #9)", () => {
    render(<ToolCallChip toolCall={chip()} />);
    expect(screen.getByText("Web search")).toBeInTheDocument();
    expect(screen.queryByText("web_search_agent")).toBeNull();
    const el = screen.getByTestId("tool-call-chip");
    // Raw name is secondary detail only — tooltip / data attribute.
    expect(el).toHaveAttribute("title", "web_search_agent");
    expect(el.querySelector(".font-mono")).toBeNull();
  });

  it("uses the server catalog label when /api/tools loads", async () => {
    fetchWithAuth.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ tools: [{ name: "knowledge_search", label: "Search knowledge bases" }] }),
      }) as unknown as Promise<never>,
    );
    render(<ToolCallChip toolCall={chip({ name: "knowledge_search" })} />);
    expect(await screen.findByText("Search knowledge bases")).toBeInTheDocument();
  });

  it("humanises a delegate AgentTool named after a skill slug", () => {
    render(<ToolCallChip toolCall={chip({ name: "demo_research_worker" })} />);
    expect(screen.getByText("Demo research worker")).toBeInTheDocument();
  });

  it("shows spinner when running", () => {
    render(<ToolCallChip toolCall={chip({ status: "running" })} />);
    expect(screen.getByLabelText("Running")).toBeInTheDocument();
  });

  it("shows checkmark on success", () => {
    render(<ToolCallChip toolCall={chip({ status: "success" })} />);
    expect(screen.getByLabelText("Success")).toBeInTheDocument();
  });

  it("shows error icon on failure", () => {
    render(<ToolCallChip toolCall={chip({ status: "error" })} />);
    expect(screen.getByLabelText("Error")).toBeInTheDocument();
  });

  it("truncates a label longer than 32 chars", () => {
    const longName = "b".repeat(40);
    render(<ToolCallChip toolCall={chip({ name: longName })} />);
    expect(screen.getByText("B" + "b".repeat(31) + "…")).toBeInTheDocument();
  });

  it("never embeds an iframe (UI surfaces are routed by MCPAppToolCallRouter, not chip)", () => {
    const { container } = render(
      <ToolCallChip toolCall={chip({ status: "success", resultContent: "plain text result" })} />,
    );
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("never embeds an iframe even when result content contains a ui:// URI", () => {
    const { container } = render(
      <ToolCallChip
        toolCall={chip({ status: "success", resultContent: "ui://app/widget" })}
      />,
    );
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("shows args and result on hover", async () => {
    const user = userEvent.setup();
    render(
      <ToolCallChip
        toolCall={chip({
          status: "success",
          argsJson: JSON.stringify({ query: "solar tariffs" }),
          resultContent: "three matches found",
        })}
      />,
    );
    expect(screen.queryByText("Called with")).toBeNull();
    await user.hover(screen.getByTestId("tool-call-chip"));
    expect(await screen.findAllByText("Called with")).not.toHaveLength(0);
    expect(screen.getAllByText("solar tariffs").length).toBeGreaterThan(0);
    expect(screen.getAllByText("three matches found").length).toBeGreaterThan(0);
    // The hover card keeps the raw tool name as secondary detail.
    expect(screen.getAllByTestId("tool-call-raw-name")[0]).toHaveTextContent("web_search_agent");
  });

  it("shows a running notice on hover before args arrive", async () => {
    const user = userEvent.setup();
    render(<ToolCallChip toolCall={chip({ status: "running" })} />);
    await user.hover(screen.getByTestId("tool-call-chip"));
    expect((await screen.findAllByText("Waiting for the tool's input…")).length).toBeGreaterThan(0);
  });
});
