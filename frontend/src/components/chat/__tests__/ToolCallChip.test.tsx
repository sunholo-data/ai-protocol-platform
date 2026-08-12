import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallChip } from "../ToolCallChip";
import type { ToolCallState } from "@/hooks/useSkillAgent";

function chip(overrides: Partial<ToolCallState> = {}): ToolCallState {
  return { id: "tc-1", name: "web_search", status: "running", ...overrides };
}

describe("ToolCallChip", () => {
  it("shows tool name", () => {
    render(<ToolCallChip toolCall={chip()} />);
    expect(screen.getByText("web_search")).toBeInTheDocument();
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

  it("truncates tool name longer than 32 chars", () => {
    const longName = "b".repeat(40);
    render(<ToolCallChip toolCall={chip({ name: longName })} />);
    expect(screen.getByText("b".repeat(32) + "…")).toBeInTheDocument();
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
});
