import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatMessageList } from "../ChatMessageList";
import type { SkillMessage } from "@/hooks/useSkillAgent";

// A2UIRenderer and MCPAppToolCallRouter mount external surfaces — stub them.
vi.mock("@/components/protocols/A2UIRenderer", () => ({
  A2UIRenderer: () => <div data-testid="a2ui-renderer" />,
}));
vi.mock("@/components/protocols/MCPAppToolCallRouter", () => ({
  MCPAppToolCallRouter: () => <div data-testid="mcp-app-router" />,
}));
// ChatPlacementForm mounts an A2UISurfaceMount (needs the registry provider) —
// stub it to a marker so we can assert interleave ORDER without the provider.
vi.mock("../ChatPlacementForms", () => ({
  ChatPlacementForm: ({ surfaceId }: { surfaceId: string }) => (
    <div data-chat-form={surfaceId}>form:{surfaceId}</div>
  ),
  useChatSurfaces: () => [],
}));

const noOp = vi.fn();

const baseProps = {
  toolCalls: [],
  thinkingContent: "",
  isThinking: false,
  isLoading: false,
  error: null,
  skillId: "my-skill",
  userInitial: "M",
  userDisplayName: "Mark",
  onAction: noOp,
};

function msg(id: string, role: SkillMessage["role"], content: string): SkillMessage {
  return { id, role, content };
}

describe("ChatMessageList", () => {
  it("renders a placeholder when there are no messages", () => {
    render(<ChatMessageList messages={[]} {...baseProps} />);
    expect(screen.getByText(/send a message/i)).toBeInTheDocument();
  });

  it("maps N messages to N bubbles", () => {
    const messages = [
      msg("u1", "user", "Hi"),
      msg("a1", "assistant", "Hello!"),
      msg("u2", "user", "How are you?"),
    ];
    render(<ChatMessageList messages={messages} {...baseProps} />);
    expect(screen.getByText("Hi")).toBeInTheDocument();
    expect(screen.getByText("Hello!")).toBeInTheDocument();
    expect(screen.getByText("How are you?")).toBeInTheDocument();
  });

  it("interleaves a chat surface ABOVE a message that arrived later (7.8 chronology)", () => {
    const messages = [msg("u1", "user", "hi")];
    const { container } = render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        // createdAt=1 → far before u1's client arrival time (~now) → leading.
        chatSurfaces={[{ surfaceId: "form-1", createdAt: 1, submitted: false, isConfirm: false, replayed: false }]}
        formSkillId="real-skill"
      />,
    );
    const form = container.querySelector('[data-chat-form="form-1"]')!;
    const bubble = screen.getByText("hi");
    expect(form).toBeInTheDocument();
    // form precedes the bubble in the DOM (rendered above it).
    expect(form.compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("interleaves a chat surface BELOW the message it followed", () => {
    const messages = [msg("u1", "user", "hi")];
    const { container } = render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        // Far-future createdAt → anchors after u1 (rendered below it).
        chatSurfaces={[{ surfaceId: "form-2", createdAt: Date.now() + 1e6, submitted: false, isConfirm: false, replayed: false }]}
      />,
    );
    const form = container.querySelector('[data-chat-form="form-2"]')!;
    const bubble = screen.getByText("hi");
    expect(bubble.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("pulls a live form BELOW the same-turn assistant text even when its createdAt precedes it", () => {
    // Real-world turn: the tool result (form) is emitted a beat BEFORE the
    // assistant narration finishes streaming, so the form's createdAt is just
    // less than the assistant message's client arrival. The form must still
    // render AFTER the intro text, not above it.
    const messages = [msg("u1", "user", "hand me to the specialist"), msg("a1", "assistant", "Sure — confirm below.")];
    const { container } = render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        // createdAt ~now: after u1/a1 client arrival is racy, but within the
        // same-turn grace of a1 → pull-forward anchors the form after a1.
        chatSurfaces={[{ surfaceId: "form-3", createdAt: Date.now() - 1, submitted: false, isConfirm: true, replayed: false }]}
        formSkillId="real-skill"
      />,
    );
    const form = container.querySelector('[data-chat-form="form-3"]')!;
    const intro = screen.getByText("Sure — confirm below.");
    // intro text precedes the form in the DOM (form rendered below it).
    expect(intro.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders a delegation marker inline after its anchor message", () => {
    const messages = [msg("u1", "user", "compare these PPAs"), msg("a1", "assistant", "Here's the analysis")];
    render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        delegations={[
          { id: "d1", afterMessageId: "u1", parent: "general-assistant", target: "ppa", targetDisplay: "PPA Specialist", avatar: null, mode: "auto", ts: 1 },
        ]}
      />,
    );
    // "PPA Specialist" now appears both as the delegation chip AND as the
    // attributed bubble header (6.11 per-delegate attribution), so match all.
    expect(screen.getAllByText("PPA Specialist").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Delegated to/i)).toBeInTheDocument();
  });

  it("renders a delegation marker whose anchor is missing as a trailing marker", () => {
    const messages = [msg("u1", "user", "hi")];
    render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        delegations={[
          { id: "d1", afterMessageId: null, parent: "p", target: "ppa", targetDisplay: "PPA Specialist", avatar: null, mode: "suggest", ts: 2 },
        ]}
      />,
    );
    expect(screen.getByText("PPA Specialist")).toBeInTheDocument();
    expect(screen.getByText(/Suggested/i)).toBeInTheDocument();
  });

  it("shows TypingIndicator dots when isLoading with no assistant message yet", () => {
    const messages = [msg("u1", "user", "Hello")];
    const { container } = render(
      <ChatMessageList messages={messages} {...baseProps} isLoading={true} />,
    );
    // TypingIndicator has three animate-bounce dots when no tool is running
    expect(container.querySelectorAll(".animate-bounce")).toHaveLength(3);
  });

  it("shows tool name in TypingIndicator when a tool call is running", () => {
    const messages = [msg("u1", "user", "Hello")];
    render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        isLoading={true}
        toolCalls={[{ id: "tc1", name: "web_search", status: "running" }]}
      />,
    );
    expect(screen.getByText("web_search")).toBeInTheDocument();
  });

  it("shows StreamingBubble when last message is assistant and isLoading", () => {
    const messages = [
      msg("u1", "user", "Hello"),
      msg("a1", "assistant", "I am typing..."),
    ];
    const { container } = render(
      <ChatMessageList messages={messages} {...baseProps} isLoading={true} />,
    );
    // StreamingBubble has the animate-pulse cursor
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows ContextBanner when activeDocumentContext is provided", () => {
    render(
      <ChatMessageList
        messages={[]}
        {...baseProps}
        activeDocumentContext={{ folderName: "Q1 Docs", docCount: 5 }}
      />,
    );
    expect(screen.getByText(/q1 docs/i)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("does not show ContextBanner when activeDocumentContext is undefined", () => {
    render(<ChatMessageList messages={[]} {...baseProps} />);
    expect(screen.queryByText(/analyzing/i)).toBeNull();
  });

  it("renders the errorBanner slot", () => {
    render(
      <ChatMessageList
        messages={[]}
        {...baseProps}
        errorBanner={<div>Stream error!</div>}
      />,
    );
    expect(screen.getByText("Stream error!")).toBeInTheDocument();
  });

  it("Bug G (chat-history-deep-fixes-3): an unparented tool call must NOT broadcast to every assistant bubble", () => {
    // Reproduces the user's report: "when we do a tool call, all chat
    // windows appear with the tool/results — not just the last one, that
    // did the toolcall."
    //
    // Pre-fix: ChatMessageList builds toolCallsByParent and falls back
    // every bubble that doesn't have its own keyed tool calls to the
    // SAME `__unparented__` array, so every assistant bubble renders the
    // same chip. Post-fix: unparented calls attach to the most recent
    // assistant message only (or none if no assistant exists yet).
    const messages = [
      msg("u1", "user", "first question"),
      msg("a1", "assistant", "first answer"),
      msg("u2", "user", "second question"),
      msg("a2", "assistant", "second answer"),
    ];
    render(
      <ChatMessageList
        messages={messages}
        {...baseProps}
        toolCalls={[
          // No parentMessageId — this is the bug class.
          { id: "tc-orphan", name: "web_search", status: "success" },
        ]}
      />,
    );

    // ToolCallChip renders the tool name as visible text. Pre-fix this
    // assertion fails because "web_search" appears in BOTH a1 and a2
    // bubbles (every non-keyed bubble falls back to __unparented__).
    const occurrences = screen.queryAllByText("web_search");
    expect(occurrences).toHaveLength(1);
  });
});

describe("ChatMessageList — transcript unavailable (regression 2026-08-05)", () => {
  // Resuming a conversation whose transcript was deleted from the session store
  // used to render an ordinary empty thread — the user picked yesterday's chat
  // and got nothing, with no explanation (CLAUDE.md #8, NEVER SILENT).
  it("renders a visible notice when the transcript is gone", () => {
    render(<ChatMessageList messages={[]} {...baseProps} transcriptUnavailable />);

    const notice = screen.getByTestId("transcript-unavailable");
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent(/no longer available/i);
  });

  it("renders nothing extra for a normal empty conversation", () => {
    render(<ChatMessageList messages={[]} {...baseProps} />);
    expect(screen.queryByTestId("transcript-unavailable")).not.toBeInTheDocument();
  });
});
