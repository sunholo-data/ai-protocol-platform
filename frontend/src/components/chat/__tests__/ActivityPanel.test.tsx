import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityPanel } from "../ActivityPanel";
import type { ToolCallState, DelegationMarkerItem } from "@/hooks/useSkillAgent";

// useSurfaceState needs the SurfaceRegistry provider; stub it (no pushed
// activity surface) and stub the A2UI mount so we test the timeline in isolation.
vi.mock("@/providers/SurfaceRegistry", () => ({ useSurfaceState: () => null }));
vi.mock("@/components/protocols/A2UISurfaceMount", () => ({
  A2UISurfaceMount: () => <div data-testid="a2ui-activity" />,
}));
// The context row resolves the model tier via /api/models — reject so it
// gracefully falls back to showing the tier name.
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: () => Promise.reject(new Error("no models in test")),
}));

const base = { sessionId: "s1" };

function tool(id: string, name: string, status: ToolCallState["status"], ts: number): ToolCallState {
  return { id, name, status, ts };
}
function deleg(id: string, targetDisplay: string, mode: DelegationMarkerItem["mode"], ts: number): DelegationMarkerItem {
  return { id, afterMessageId: null, parent: "p", target: id, targetDisplay, avatar: null, mode, ts };
}

describe("ActivityPanel", () => {
  it("shows an empty state when there is no activity", () => {
    render(<ActivityPanel toolCalls={[]} delegations={[]} isThinking={false} {...base} />);
    expect(screen.getByText(/shows up here as it works/i)).toBeInTheDocument();
  });

  it("renders tool calls and delegations together", () => {
    render(
      <ActivityPanel
        toolCalls={[tool("t1", "web_search", "success", 10)]}
        delegations={[deleg("d1", "PPA Specialist", "auto", 20)]}
        isThinking={false}
        {...base}
      />,
    );
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText("PPA Specialist")).toBeInTheDocument();
    expect(screen.getByText(/Delegated to/i)).toBeInTheDocument();
  });

  it("collapses internal plumbing (transfer_to_agent) under a disclosure (6.11)", () => {
    render(
      <ActivityPanel
        toolCalls={[
          tool("t1", "transfer_to_agent", "success", 10),
          tool("t2", "transfer_to_agent", "success", 11),
          tool("t3", "web_search", "success", 12),
        ]}
        delegations={[]}
        isThinking={false}
        {...base}
      />,
    );
    // A useful tool shows; the internal hops are hidden behind a toggle.
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.queryByText("transfer_to_agent")).not.toBeInTheDocument();
    const toggle = screen.getByTestId("activity-internal-toggle");
    expect(toggle).toHaveTextContent("Show 2 internal steps");
    fireEvent.click(toggle);
    expect(screen.getAllByText("transfer_to_agent")).toHaveLength(2);
    expect(toggle).toHaveTextContent("Hide 2 internal steps");
  });

  it("shows no internal-steps toggle when there is no plumbing", () => {
    render(
      <ActivityPanel
        toolCalls={[tool("t1", "web_search", "success", 10)]}
        delegations={[]}
        isThinking={false}
        {...base}
      />,
    );
    expect(screen.queryByTestId("activity-internal-toggle")).not.toBeInTheDocument();
  });

  it("shows a Reasoning row while thinking", () => {
    render(<ActivityPanel toolCalls={[]} delegations={[]} isThinking={true} {...base} />);
    expect(screen.getByText(/Reasoning/i)).toBeInTheDocument();
  });

  it("shows a human-friendly relative timestamp", () => {
    render(
      <ActivityPanel
        toolCalls={[tool("t1", "web_search", "running", Date.now())]}
        delegations={[]}
        isThinking={false}
        {...base}
      />,
    );
    expect(screen.getByText(/just now|s ago/i)).toBeInTheDocument();
  });

  it("expands a tool row to reveal what it was called with", () => {
    const tc: ToolCallState = {
      id: "t1",
      name: "web_search",
      status: "success",
      ts: Date.now(),
      argsJson: '{"query":"power purchase agreement"}',
      resultContent: "3 results found",
    };
    render(<ActivityPanel toolCalls={[tc]} delegations={[]} isThinking={false} {...base} />);
    // Collapsed: args not shown yet.
    expect(screen.queryByText(/Called with/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /web_search/i }));
    expect(screen.getByText("Called with")).toBeInTheDocument();
    expect(screen.getByText(/power purchase agreement/i)).toBeInTheDocument();
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(screen.getByText(/3 results found/i)).toBeInTheDocument();
  });

  it("renders args as a key→value list, not a JSON blob", () => {
    const tc: ToolCallState = {
      id: "t1",
      name: "compare_ppa_contracts",
      status: "success",
      ts: Date.now(),
      argsJson: '{"left_doc_id":"abc-123","right_doc_id":"def-456"}',
    };
    render(<ActivityPanel toolCalls={[tc]} delegations={[]} isThinking={false} {...base} />);
    fireEvent.click(screen.getByRole("button", { name: /compare_ppa_contracts/i }));
    expect(screen.getByText("left_doc_id")).toBeInTheDocument();
    expect(screen.getByText("abc-123")).toBeInTheDocument();
    expect(screen.getByText("right_doc_id")).toBeInTheDocument();
  });

  it("deep-unwraps a double-encoded result into clean, unescaped text", () => {
    const tc: ToolCallState = {
      id: "t1",
      name: "compare_ppa_contracts",
      status: "success",
      ts: Date.now(),
      resultContent: '{"result": "{\\"left\\":{\\"name\\":\\"EDP\\"}}"}',
    };
    render(<ActivityPanel toolCalls={[tc]} delegations={[]} isThinking={false} {...base} />);
    fireEvent.click(screen.getByRole("button", { name: /compare_ppa_contracts/i }));
    // Rendered as a structured tree: nested keys + clean, unescaped string value.
    expect(screen.getByText("left:")).toBeInTheDocument();
    expect(screen.getByText("name:")).toBeInTheDocument();
    expect(screen.getByText('"EDP"')).toBeInTheDocument(); // quoted, not escaped
    // The `{ result: ... }` envelope is hoisted away — no such key node.
    expect(screen.queryByText("result:")).toBeNull();
  });

  it("shows a context row with the model tier and read-aloud config", () => {
    render(
      <ActivityPanel
        toolCalls={[]}
        delegations={[]}
        isThinking={false}
        {...base}
        context={{ modelTier: "smart", voice: { enabled: true, language: "es" } }}
      />,
    );
    expect(screen.getByText("smart")).toBeInTheDocument(); // model (falls back to tier)
    expect(screen.getByText("on")).toBeInTheDocument(); // read-aloud on
  });

  it("renders session documents as 'Added' entries", () => {
    render(
      <ActivityPanel
        toolCalls={[]}
        delegations={[]}
        isThinking={false}
        {...base}
        documents={[{ id: "d1", name: "contract.pdf", ts: 5 }]}
        sessionStartTs={1}
      />,
    );
    expect(screen.getByText("contract.pdf")).toBeInTheDocument();
  });

  it("renders a Session started marker", () => {
    render(
      <ActivityPanel toolCalls={[]} delegations={[]} isThinking={false} {...base} sessionStartTs={1} />,
    );
    expect(screen.getByText(/Session started/i)).toBeInTheDocument();
  });

  it("shows a live Running row while an action-triggered run is in flight (even before its first tool call)", () => {
    render(
      <ActivityPanel toolCalls={[]} delegations={[]} isThinking={false} {...base} isRunning />,
    );
    // The empty-state invitation is replaced by a live running indicator.
    expect(screen.queryByText(/shows up here as it works/i)).toBeNull();
    expect(screen.getByTestId("activity-running-row")).toBeInTheDocument();
    expect(screen.getByText("Running…")).toBeInTheDocument();
  });

  it("shows the server stage label on the running row when present", () => {
    render(
      <ActivityPanel
        toolCalls={[]}
        delegations={[]}
        isThinking={false}
        {...base}
        isRunning
        runStageLabel="Reading 2 documents…"
      />,
    );
    expect(screen.getByText("Reading 2 documents…")).toBeInTheDocument();
  });

  it("surfaces an action-run error as an error row (not just a console warning)", () => {
    render(
      <ActivityPanel
        toolCalls={[]}
        delegations={[]}
        isThinking={false}
        {...base}
        runError="Action-triggered run failed: mapper crashed"
      />,
    );
    expect(screen.getByTestId("activity-error-row")).toBeInTheDocument();
    expect(screen.getByText(/mapper crashed/i)).toBeInTheDocument();
  });

  it("orders newest activity first", () => {
    render(
      <ActivityPanel
        toolCalls={[tool("t1", "older_tool", "success", 10)]}
        delegations={[deleg("d1", "Newer Specialist", "auto", 99)]}
        isThinking={false}
        {...base}
      />,
    );
    const items = screen.getAllByRole("listitem");
    // Newest (the delegation at ts=99) renders before the older tool.
    expect(items[0].textContent).toContain("Newer Specialist");
    expect(items[1].textContent).toContain("older_tool");
  });
});
