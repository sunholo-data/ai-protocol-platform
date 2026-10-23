import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Proposal } from "@/components/studio/applyProposal";

// --- Mock the AG-UI chat layer so no real network / SSE happens ---
// The component consumes useSkillAgent(); we return a fixed message list whose
// assistant turn carries a fenced proposals block.
const assistantContent = [
  "Sure — here's a proposal:",
  "```json",
  JSON.stringify({
    proposals: [
      { kind: "set_display_name", label: "Name it Reviewer", value: "Reviewer" },
    ],
  }),
  "```",
].join("\n");

let svgContent = "";
let running = false;
const sendMessage = vi.fn(async () => {});

vi.mock("@/hooks/useSkillAgent", () => ({
  useSkillAgent: () => ({
    sessionId: "copilot-thread-1",
    messages: [
      { id: "u1", role: "user", content: "make a contract reviewer" },
      { id: "a1", role: "assistant", content: assistantContent + svgContent },
    ],
    toolCalls: [],
    thinkingContent: "",
    isThinking: false,
    stageLabel: null,
    delegations: [],
    sendMessage,
    isLoading: running,
    error: null,
    clearError: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/lib/visualEvidence", () => ({ captureSvg: vi.fn(), sourceDigest: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
import { captureSvg, sourceDigest } from "@/lib/visualEvidence";
import { AuthoringCopilot } from "@/components/studio/AuthoringCopilot";

describe("AuthoringCopilot", () => {
  const fetchSpy = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    svgContent = ""; running = false;
    vi.stubEnv("NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED", "true");
    // Track any accidental network call.
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  it("renders a proposal card from an assistant proposals block", () => {
    render(<AuthoringCopilot skillId="skill-123" onApplyProposal={vi.fn()} />);
    expect(screen.getByText("Name it Reviewer")).toBeInTheDocument();
    expect(screen.getByTestId("proposal-card")).toBeInTheDocument();
  });

  it("Apply calls onApplyProposal with the parsed proposal and does NOT fetch", () => {
    const onApply = vi.fn((_p: Proposal) => {});
    render(<AuthoringCopilot skillId="skill-123" onApplyProposal={onApply} />);

    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "set_display_name", value: "Reviewer" }),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(fetchWithAuth).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("Dismiss removes the card", () => {
    render(<AuthoringCopilot skillId="skill-123" onApplyProposal={vi.fn()} />);
    expect(screen.getByTestId("proposal-card")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(screen.queryByTestId("proposal-card")).not.toBeInTheDocument();
  });
});


describe("AuthoringCopilot visual evidence", () => {
  beforeEach(() => {
    vi.clearAllMocks(); running = false;
    vi.stubEnv("NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED", "true");
    svgContent = '\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><text x="10" y="30">Synthetic Studio fixture</text></svg>\n```';
    vi.mocked(captureSvg).mockResolvedValue({ sourceRevision: "revision", pngBase64: "bytes", width: 200, height: 100, captureMode: "client_supplied_isolated_render" });
    vi.mocked(sourceDigest).mockResolvedValue("revision");
    vi.mocked(fetchWithAuth).mockResolvedValue({ok:true,json:async()=>({status:"inspected",source_revision:"revision",findings:{observations:["Fixture label visible"],limitations:["Isolated preview"]}})} as Response);
  });
  afterEach(() => { vi.unstubAllEnvs(); svgContent = ""; running = false; });

  it("renders assistant SVG with the existing inspector and requires explicit correction request", async () => {
    const onApply = vi.fn();
    render(<AuthoringCopilot skillId="skill" onApplyProposal={onApply} />);
    await screen.findByText("Synthetic Studio fixture");
    fireEvent.click(await screen.findByText("Inspect diagram"));
    await screen.findByText("Fixture label visible");
    expect(JSON.parse(vi.mocked(fetchWithAuth).mock.calls[0][1]!.body as string).session_id).toBe("copilot-thread-1");
    expect(sendMessage).not.toHaveBeenCalled(); expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Ask copilot to revise"));
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0]).toEqual([expect.stringContaining("Do not save or publish")]);
  });

  it("renders the SVG with inspection disabled when the flag is off", async () => {
    vi.stubEnv("NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED", "false");
    render(<AuthoringCopilot skillId="skill" onApplyProposal={vi.fn()} />);
    await screen.findByText("Synthetic Studio fixture");
    expect(screen.queryByText("Inspect diagram")).toBeNull();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("prevents correction requests during an active copilot run", async () => {
    const view = render(<AuthoringCopilot skillId="skill" onApplyProposal={vi.fn()} />);
    fireEvent.click(await screen.findByText("Inspect diagram"));
    await screen.findByText("Ask copilot to revise");
    running = true;
    view.rerender(<AuthoringCopilot skillId="skill" onApplyProposal={vi.fn()} />);
    expect(screen.queryByText("Ask copilot to revise")).toBeNull();
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
