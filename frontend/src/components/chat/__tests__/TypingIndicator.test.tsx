import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TypingIndicator } from "../TypingIndicator";
import { BRANDING } from "@/lib/branding";

describe("TypingIndicator", () => {
  it("renders three animated dots when no tool is active", () => {
    const { container } = render(<TypingIndicator />);
    const dots = container.querySelectorAll("[class*=animate-bounce]");
    expect(dots).toHaveLength(3);
  });

  it("renders the bot avatar image with branded alt text", () => {
    render(<TypingIndicator />);
    expect(screen.getByRole("img", { name: BRANDING.appName })).toBeInTheDocument();
  });

  it("shows the friendly tool label when activeToolName is provided", () => {
    render(<TypingIndicator activeToolName="web_search_agent" />);
    expect(screen.getByText("Web search")).toBeInTheDocument();
    expect(screen.queryAllByRole("presentation").length).toBeGreaterThanOrEqual(0);
  });

  it("hides three dots when activeToolName is provided", () => {
    const { container } = render(<TypingIndicator activeToolName="list_documents" />);
    expect(container.querySelectorAll("[class*=animate-bounce]")).toHaveLength(0);
  });

  // ttft-instrumentation.md M2: STAGE_PROGRESS labels surface the
  // backend's per-stage progress (Reading 2 documents…, Thinking…,
  // Calling search…) inside the typing indicator before any model
  // token lands. Decouples perceived TTFT from real model TTFT.
  describe("stageLabel (server-authored stage progress)", () => {
    it("renders stageLabel when provided", () => {
      render(<TypingIndicator stageLabel="Reading 2 documents…" />);
      expect(screen.getByText("Reading 2 documents…")).toBeInTheDocument();
    });

    it("hides three dots when stageLabel is provided", () => {
      const { container } = render(<TypingIndicator stageLabel="Thinking…" />);
      expect(container.querySelectorAll("[class*=animate-bounce]")).toHaveLength(0);
    });

    it("stageLabel takes priority over activeToolName", () => {
      // The server-authored label is canonical — if the backend has
      // emitted a STAGE_PROGRESS, that's what the user should see,
      // not the local toolName fallback.
      render(<TypingIndicator stageLabel="Calling web_search…" activeToolName="other_tool" />);
      expect(screen.getByText("Calling web_search…")).toBeInTheDocument();
      expect(screen.queryByText("other_tool")).not.toBeInTheDocument();
    });

    it("falls back to activeToolName when stageLabel is null", () => {
      render(<TypingIndicator stageLabel={null} activeToolName="search_documents" />);
      expect(screen.getByText("Search your documents")).toBeInTheDocument();
    });

    it("falls back to dots when both stageLabel and activeToolName are absent", () => {
      const { container } = render(<TypingIndicator stageLabel={null} />);
      expect(container.querySelectorAll("[class*=animate-bounce]")).toHaveLength(3);
    });
  });
});
