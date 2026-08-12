import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageBubble } from "../MessageBubble";
import type { SkillMessage } from "@/hooks/useSkillAgent";
import { BRANDING } from "@/lib/branding";

vi.mock("@/components/protocols/A2UIRenderer", () => ({
  A2UIRenderer: () => <div data-testid="a2ui-renderer" />,
}));
const mcpRouterMock = vi.fn();
vi.mock("@/components/protocols/MCPAppToolCallRouter", () => ({
  MCPAppToolCallRouter: (props: Record<string, unknown>) => {
    mcpRouterMock(props);
    return <div data-testid="mcp-app-router" />;
  },
}));

const noOp = vi.fn();
const noopNavigate = vi.fn();

function botMsg(content = "Hello from bot"): SkillMessage {
  return { id: "bot-1", role: "assistant", content };
}

function userMsg(content = "Hello from user"): SkillMessage {
  return { id: "user-1", role: "user", content };
}

const baseProps = {
  skillId: "test-skill",
  userInitial: "M",
  userDisplayName: "Mark",
  toolCalls: [],
  navigateToBlock: noopNavigate,
  onAction: noOp,
};

describe("MessageBubble — bot variant", () => {
  it("renders message content", () => {
    render(<MessageBubble message={botMsg()} {...baseProps} />);
    expect(screen.getByText(/hello from bot/i)).toBeInTheDocument();
  });

  it("shows skill name in header", () => {
    render(<MessageBubble message={botMsg()} {...baseProps} />);
    expect(screen.getByText("test-skill")).toBeInTheDocument();
  });

  it("applies orange left border", () => {
    const { container } = render(<MessageBubble message={botMsg()} {...baseProps} />);
    expect(container.querySelector(".border-orange-400")).toBeInTheDocument();
  });

  it("renders bot avatar image with branded alt text", () => {
    render(<MessageBubble message={botMsg()} {...baseProps} />);
    expect(screen.getByRole("img", { name: BRANDING.appName })).toBeInTheDocument();
  });

  it("uses the skill's avatar when provided so the bubble matches the switcher (6.11)", () => {
    render(<MessageBubble message={botMsg()} {...baseProps} botAvatarUrl="/images/logo/aitana-logo.svg" />);
    const img = screen.getByRole("img", { name: "test-skill" });
    expect(img).toHaveAttribute("src", "/images/logo/aitana-logo.svg");
  });

  it("shows a timestamp", () => {
    render(<MessageBubble message={botMsg()} {...baseProps} />);
    // Timestamp is in AM/PM format — just check something time-like exists
    expect(document.body.textContent).toMatch(/\d{1,2}:\d{2}/);
  });

  it("renders the ORIGINAL send time from the timestamp prop, not load time", () => {
    // Regression: resumed history showed the load time. The bubble must format
    // the passed epoch-ms timestamp, not `new Date()`.
    // 2000-01-01T09:41:00Z — pick a fixed instant and assert the H:MM renders.
    const ts = new Date("2000-01-01T09:41:00Z").getTime();
    const expected = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" }).format(new Date(ts));
    render(<MessageBubble message={botMsg()} {...baseProps} timestamp={ts} />);
    expect(document.body.textContent).toContain(expected);
    expect(expected).toMatch(/9:41|41/); // sanity: minute is preserved (tz-independent)
  });
});

describe("MessageBubble — user variant", () => {
  it("renders message content", () => {
    render(<MessageBubble message={userMsg()} {...baseProps} />);
    expect(screen.getByText(/hello from user/i)).toBeInTheDocument();
  });

  it("shows user display name in header", () => {
    render(<MessageBubble message={userMsg()} {...baseProps} />);
    expect(screen.getByText("Mark")).toBeInTheDocument();
  });

  it("applies primary-theme left border (G34: no hardcoded teal)", () => {
    const { container } = render(<MessageBubble message={userMsg()} {...baseProps} />);
    expect(container.querySelector(".border-primary")).toBeInTheDocument();
    // Pin the regression: no hardcoded teal class should survive.
    expect(container.querySelector(".border-teal-500")).not.toBeInTheDocument();
    expect(container.querySelector(".from-teal-400")).not.toBeInTheDocument();
  });

  it("renders initial avatar letter when no photoURL provided", () => {
    render(<MessageBubble message={userMsg()} {...baseProps} />);
    expect(screen.getByText("M")).toBeInTheDocument();
  });

  it("renders user photo when userPhotoURL is provided (G34)", () => {
    const photoURL = "https://example.com/avatar.jpg";
    render(
      <MessageBubble
        message={userMsg()}
        {...baseProps}
        userPhotoURL={photoURL}
      />,
    );
    const img = screen.getByRole("img", { name: "Mark" }) as HTMLImageElement;
    expect(img.src).toBe(photoURL);
    // Initial chip should NOT also render when photo is present.
    expect(screen.queryByText("M")).not.toBeInTheDocument();
  });

  it("falls back to initial chip when userPhotoURL is null (G34 null safety)", () => {
    render(
      <MessageBubble
        message={userMsg()}
        {...baseProps}
        userPhotoURL={null}
      />,
    );
    expect(screen.getByText("M")).toBeInTheDocument();
  });
});

describe("MessageBubble — tool call chips", () => {
  it("renders running tool call chip with tool name", () => {
    const toolCalls = [{ id: "tc-1", name: "web_search", status: "running" as const }];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);
    expect(screen.getByText("web_search")).toBeInTheDocument();
  });

  it("truncates long tool names to 32 chars", () => {
    const longName = "a".repeat(40);
    const toolCalls = [{ id: "tc-1", name: longName, status: "running" as const }];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);
    expect(screen.getByText("a".repeat(32) + "…")).toBeInTheDocument();
  });
});

describe("MessageBubble — A2UI tool delivery", () => {
  it("renders A2UIRenderer for send_a2ui_json_to_client result", () => {
    const payload = { component: "form", fields: [] };
    const toolCalls = [
      {
        id: "tc-a2ui",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ validated_a2ui_json: payload }),
      },
    ];
    const { container } = render(
      <MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />,
    );
    // A2UIRenderer mocked to data-testid="a2ui-renderer"
    expect(container.querySelector("[data-testid='a2ui-renderer']")).toBeInTheDocument();
  });

  it("does not render ToolCallChip for send_a2ui_json_to_client", () => {
    const toolCalls = [
      {
        id: "tc-a2ui",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ validated_a2ui_json: { component: "table" } }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);
    // ToolCallChip shows the tool name — should NOT appear for A2UI calls
    expect(screen.queryByText("send_a2ui_json_to_client")).not.toBeInTheDocument();
  });

  it("renders ToolCallChip for non-A2UI tool calls", () => {
    const toolCalls = [
      { id: "tc-1", name: "web_search", status: "success" as const },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.queryByTestId("a2ui-renderer")).not.toBeInTheDocument();
  });

  it("does not render A2UIRenderer when resultContent is missing", () => {
    const toolCalls = [
      { id: "tc-a2ui", name: "send_a2ui_json_to_client", status: "running" as const },
    ];
    const { container } = render(
      <MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />,
    );
    expect(container.querySelector("[data-testid='a2ui-renderer']")).not.toBeInTheDocument();
  });
});

describe("MessageBubble — MCP App tool routing", () => {
  it("invokes MCPAppToolCallRouter with non-A2UI tool calls that have resultContent", () => {
    mcpRouterMock.mockClear();
    const toolCalls = [
      {
        id: "tc-show-map",
        name: "show-map",
        status: "success" as const,
        resultContent: JSON.stringify({ content: [{ type: "text", text: "ok" }] }),
      },
      {
        id: "tc-a2ui",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ validated_a2ui_json: { component: "x" } }),
      },
    ];
    render(
      <MessageBubble
        message={botMsg()}
        {...baseProps}
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );
    expect(mcpRouterMock).toHaveBeenCalledOnce();
    const props = mcpRouterMock.mock.calls[0][0] as {
      toolCalls: Array<{ id: string }>;
      mcpServerIds: readonly string[];
    };
    // A2UI tool is filtered out before hitting the router; show-map remains.
    expect(props.toolCalls.map((tc) => tc.id)).toEqual(["tc-show-map"]);
    expect(props.mcpServerIds).toEqual(["map-server"]);
  });

  it("does NOT invoke MCPAppToolCallRouter when no candidates have resultContent", () => {
    mcpRouterMock.mockClear();
    const toolCalls = [
      // running tool — no resultContent yet
      { id: "tc-running", name: "show-map", status: "running" as const },
    ];
    render(
      <MessageBubble
        message={botMsg()}
        {...baseProps}
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );
    expect(mcpRouterMock).not.toHaveBeenCalled();
  });

  it("forwards onChatMessage prop to MCPAppToolCallRouter", () => {
    mcpRouterMock.mockClear();
    const onChatMessage = vi.fn();
    const toolCalls = [
      {
        id: "tc-show-map",
        name: "show-map",
        status: "success" as const,
        resultContent: JSON.stringify({ content: [] }),
      },
    ];
    render(
      <MessageBubble
        message={botMsg()}
        {...baseProps}
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
        onChatMessage={onChatMessage}
      />,
    );
    const props = mcpRouterMock.mock.calls[0][0] as {
      onChatMessage: (s: string) => void;
    };
    expect(props.onChatMessage).toBe(onChatMessage);
  });
});
