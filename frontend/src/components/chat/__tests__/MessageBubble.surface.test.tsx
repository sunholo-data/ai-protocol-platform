// MessageBubble — surface routing tests (v0.9 native)
//
// After the v0.9 rewrite, the envelope's `validated_a2ui_json` is an ARRAY
// of A2UI v0.9 messages. The dispatcher pushes that array straight to
// SurfaceRegistry.appendMessages — no patch/replace branch (the messages
// themselves encode intent).
//
// We mock A2UIRenderer + useSurfaceRegistry so tests can:
//   - Detect inline render via the renderer's data-testid
//   - Assert dispatch via the appendMessages spy

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessageBubble } from "../MessageBubble";
import type { SkillMessage } from "@/hooks/useSkillAgent";

vi.mock("@/components/protocols/A2UIRenderer", () => ({
  A2UIRenderer: () => <div data-testid="a2ui-renderer" />,
}));

vi.mock("@/components/protocols/MCPAppToolCallRouter", () => ({
  MCPAppToolCallRouter: () => <div data-testid="mcp-app-router" />,
}));

const appendMessagesMock = vi.fn();

vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceRegistry: () => ({
    appendMessages: appendMessagesMock,
    register: vi.fn(),
    unregister: vi.fn(),
    getMount: vi.fn(),
    getPolicy: vi.fn(),
    clearSurface: vi.fn(),
    clearByPersistence: vi.fn(),
    getState: vi.fn(),
  }),
}));

const noOp = vi.fn();
const noopNavigate = vi.fn();

function botMsg(content = "ok"): SkillMessage {
  return { id: "bot-1", role: "assistant", content };
}

const baseProps = {
  skillId: "test-skill",
  userInitial: "M",
  userDisplayName: "Mark",
  toolCalls: [],
  navigateToBlock: noopNavigate,
  onAction: noOp,
};

// One realistic v0.9 dashboard message stream — the SDK envelope wraps
// this array as `validated_a2ui_json`.
const dashboardMessages = [
  {
    version: "v0.9",
    createSurface: {
      surfaceId: "workspace",
      catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json",
    },
  },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: "workspace",
      components: [
        { id: "root", component: "Column", children: ["title"] },
        { id: "title", component: "Text", text: "Hello" },
      ],
    },
  },
];

beforeEach(() => {
  appendMessagesMock.mockReset();
});

describe("MessageBubble — surface routing (v0.9)", () => {
  it("dispatches workspace-surface messages to the registry (no inline render)", () => {
    const toolCalls = [
      {
        id: "tc-ws",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "workspace",
          update_mode: "replace",
        }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).toHaveBeenCalledWith(
      "workspace",
      dashboardMessages,
      "tc-ws",
    );
    expect(screen.queryByTestId("a2ui-renderer")).not.toBeInTheDocument();
  });

  it("renders inline (no dispatch) when surface_id is absent", () => {
    const toolCalls = [
      {
        id: "tc-legacy",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ validated_a2ui_json: dashboardMessages }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("a2ui-renderer")).toBeInTheDocument();
  });

  it("renders inline when surface_id is explicitly 'chat'", () => {
    const toolCalls = [
      {
        id: "tc-chat",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "chat",
        }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("a2ui-renderer")).toBeInTheDocument();
  });

  it("treats a single-message envelope (non-array) as a one-element array", () => {
    // Defensive: the SDK's parse_and_fix wraps a single object in a list
    // before validating, but older envelopes might land as a bare object.
    const single = dashboardMessages[0];
    const toolCalls = [
      {
        id: "tc-single",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: single,
          surface_id: "workspace",
        }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).toHaveBeenCalledWith(
      "workspace",
      [single],
      "tc-single",
    );
  });

  it("dispatches each surface call independently when multiple coexist", () => {
    const toolCalls = [
      {
        id: "tc-ws",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "workspace",
        }),
      },
      {
        id: "tc-sb",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "sidebar",
        }),
      },
      {
        id: "tc-chat",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ validated_a2ui_json: dashboardMessages }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).toHaveBeenCalledWith(
      "workspace",
      dashboardMessages,
      "tc-ws",
    );
    expect(appendMessagesMock).toHaveBeenCalledWith(
      "sidebar",
      dashboardMessages,
      "tc-sb",
    );
    expect(appendMessagesMock).toHaveBeenCalledTimes(2);
    expect(screen.getAllByTestId("a2ui-renderer")).toHaveLength(1);
  });

  it("suppresses the visible bubble when assistant has no text and all A2UI calls are routed off-chat", () => {
    // Tool-only assistant turn whose ONLY content is a workspace-routed
    // A2UI call. Pre-fix, this rendered an empty avatar+name+timestamp
    // row (see screenshot 2026-05-18). Post-fix, the dispatcher still
    // fires (so the workspace pane gets the messages) but the bubble's
    // visible chrome doesn't render — nothing to show inline.
    const toolCalls = [
      {
        id: "tc-suppress",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "workspace",
        }),
      },
    ];
    const { container } = render(
      <MessageBubble
        message={{ ...botMsg(""), content: "" }}
        {...baseProps}
        toolCalls={toolCalls}
      />,
    );

    // Dispatcher still fired.
    expect(appendMessagesMock).toHaveBeenCalledWith(
      "workspace",
      dashboardMessages,
      "tc-suppress",
    );
    // But no bubble chrome rendered — no skill-name label, no
    // timestamp, no avatar.
    expect(container.querySelector("[data-testid='a2ui-renderer']")).not.toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain("test-skill");
  });

  it("renders the visible bubble when assistant has text content alongside a routed A2UI call", () => {
    // Mixed turn: text reply + workspace-routed A2UI. The bubble should
    // render (to show the text) AND the dispatcher should fire.
    const toolCalls = [
      {
        id: "tc-mix",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({
          validated_a2ui_json: dashboardMessages,
          surface_id: "workspace",
        }),
      },
    ];
    const { container } = render(
      <MessageBubble
        message={botMsg("Dashboard rendered in the workspace pane.")}
        {...baseProps}
        toolCalls={toolCalls}
      />,
    );
    expect(appendMessagesMock).toHaveBeenCalledWith(
      "workspace",
      dashboardMessages,
      "tc-mix",
    );
    expect(container.textContent ?? "").toContain("test-skill");
    expect(container.textContent ?? "").toContain("Dashboard rendered");
  });

  it("drops envelopes with no validated_a2ui_json", () => {
    const toolCalls = [
      {
        id: "tc-bad",
        name: "send_a2ui_json_to_client",
        status: "success" as const,
        resultContent: JSON.stringify({ error: "validation failed" }),
      },
    ];
    render(<MessageBubble message={botMsg()} {...baseProps} toolCalls={toolCalls} />);

    expect(appendMessagesMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("a2ui-renderer")).not.toBeInTheDocument();
  });
});
