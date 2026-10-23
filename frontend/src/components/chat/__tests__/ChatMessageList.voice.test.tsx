import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SkillMessage } from "@/hooks/useSkillAgent";

vi.mock("../MessageBubble", () => ({ MessageBubble: ({ message, autoSpeakAllowed }: {
  message: SkillMessage; autoSpeakAllowed?: boolean;
}) => <div data-testid={message.id} data-auto-read={!!autoSpeakAllowed}>{message.content}</div> }));
vi.mock("../ChatPlacementForms", () => ({ ChatPlacementForm: () => null, useChatSurfaces: () => [] }));
import { ChatMessageList } from "../ChatMessageList";

const base = { toolCalls: [], thinkingContent: "", isThinking: false, isLoading: false,
  error: null, skillId: "skill", userInitial: "U", userDisplayName: "User", onAction: vi.fn(), sessionId: "session" };
const msg = (id: string, role: SkillMessage["role"], content: string): SkillMessage => ({ id, role, content });

describe("auto-read completion gate", () => {
  it("never reads loaded history; reads only the newly completed reply", async () => {
    const history = [msg("old", "assistant", "History")];
    const { rerender } = render(<ChatMessageList {...base} messages={history} />);
    expect(screen.getByTestId("old")).toHaveAttribute("data-auto-read", "false");
    const asked = [...history, msg("user", "user", "Question")];
    rerender(<ChatMessageList {...base} isLoading messages={asked} />);
    const replied = [...asked, msg("new", "assistant", "Reply")];
    rerender(<ChatMessageList {...base} isLoading messages={replied} />);
    rerender(<ChatMessageList {...base} messages={replied} completedReplyId="new" />);
    await waitFor(() => expect(screen.getByTestId("new")).toHaveAttribute("data-auto-read", "true"));
    expect(screen.getByTestId("old")).toHaveAttribute("data-auto-read", "false");
    rerender(<ChatMessageList {...base} sessionId="other" messages={replied} />);
    await waitFor(() => expect(screen.getByTestId("new")).toHaveAttribute("data-auto-read", "false"));
  });

  it("does not speak an interrupted or failed reply", async () => {
    const { rerender } = render(<ChatMessageList {...base} messages={[msg("u", "user", "Question")]} isLoading />);
    rerender(<ChatMessageList {...base} error={{ kind: "network", message: "Interrupted", rawMessage: "Interrupted", retryable: true }} messages={[msg("a", "assistant", "Partial answer")]} />);
    expect(screen.getByTestId("a")).toHaveAttribute("data-auto-read", "false");
  });
});
