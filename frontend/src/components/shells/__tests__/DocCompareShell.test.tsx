import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { SkillShell } from "@/types/skill";

// --- controllable mock state ---
let surfacePresent = false;
const sendMessage = vi.fn();

vi.mock("@/hooks/useSkillAgent", () => ({
  useSkillAgent: () => ({
    sessionId: "agent-session-1",
    messages: [],
    toolCalls: [],
    thinkingContent: "",
    isThinking: false,
    stageLabel: null,
    delegations: [],
    sendMessage,
    isLoading: false,
    error: null,
    clearError: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock("@/hooks/useSkillMeta", () => ({
  useSkillMeta: () => ({
    displayName: "PPA Contract Compare",
    mcpServerIds: [],
    welcome: { introMessage: "Pick two contracts to compare." },
    initialMessage: "",
    slug: null,
    ownerId: null,
    loading: false,
  }),
}));

vi.mock("@/hooks/useSessionMessages", () => ({
  useSessionMessages: () => ({ initialMessages: [], historyError: null, sessionGone: false }),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("@/components/chat/ChatMessageList", () => ({
  ChatMessageList: () => <div data-testid="chat-message-list" />,
}));

vi.mock("@/components/protocols/A2UISurfaceMount", () => ({
  A2UISurfaceMount: ({ surfaceId }: { surfaceId: string }) => (
    <div data-testid="a2ui-surface-mount" data-surface={surfaceId} />
  ),
}));

vi.mock("@/providers/SurfaceRegistry", () => ({
  SurfaceRegistryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useClearSurfacesOnSessionChange: () => undefined,
  useSurfaceState: () => (surfacePresent ? { surface: { id: "workspace" } } : null),
}));

import { DocCompareShell } from "@/components/shells/DocCompareShell";

const baseProps = {
  skillId: "skill-1",
  pathPrefix: "/chat/@owner/one-doc-compare",
  user: { uid: "u1", displayName: "Mark", email: "mark@x.com", photoURL: null } as never,
};

function renderShell(shell: SkillShell | null) {
  return render(<DocCompareShell {...baseProps} shell={shell} />);
}

describe("DocCompareShell", () => {
  beforeEach(() => {
    surfacePresent = false;
    sendMessage.mockClear();
  });

  it("shows the empty prompt when no workspace surface has been rendered yet", () => {
    renderShell({ mode: "doc-compare" });
    expect(screen.getByTestId("doc-compare-empty")).not.toBeNull();
    expect(screen.queryByTestId("a2ui-surface-mount")).toBeNull();
  });

  it("fills the canvas with the A2UI workspace surface once present", () => {
    surfacePresent = true;
    renderShell({ mode: "doc-compare" });
    const mount = screen.getByTestId("a2ui-surface-mount");
    expect(mount.getAttribute("data-surface")).toBe("workspace");
    expect(screen.queryByTestId("doc-compare-empty")).toBeNull();
  });

  it("docks chat in a right-side drawer", () => {
    renderShell({ mode: "doc-compare" });
    const drawer = screen.getByTestId("chat-drawer");
    expect(drawer.getAttribute("data-side")).toBe("right");
    expect(screen.getByTestId("chat-message-list")).not.toBeNull();
  });

  it("starts the drawer collapsed by default and open when shell requests it", () => {
    const { unmount } = renderShell({ mode: "doc-compare" });
    expect(screen.getByTestId("chat-drawer").getAttribute("data-open")).toBe("false");
    unmount();

    renderShell({
      mode: "doc-compare",
      chat: { position: "right-drawer", defaultState: "open" },
    });
    expect(screen.getByTestId("chat-drawer").getAttribute("data-open")).toBe("true");
  });

  it("sends a chat message from the drawer composer", () => {
    renderShell({ mode: "doc-compare", chat: { position: "right-drawer", defaultState: "open" } });
    const input = screen.getByPlaceholderText("Ask about the comparison…");
    fireEvent.change(input, { target: { value: "Why does clause 7 differ?" } });
    fireEvent.submit(input.closest("form")!);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0][0]).toBe("Why does clause 7 differ?");
  });

  it("falls back to chat-primary handling is not its concern — renders without a shell block", () => {
    renderShell(null);
    // No shell → drawerState minimised; the shell still renders its canvas + drawer.
    expect(screen.getByTestId("chat-drawer")).not.toBeNull();
    expect(screen.getByTestId("doc-compare-empty")).not.toBeNull();
  });
});
