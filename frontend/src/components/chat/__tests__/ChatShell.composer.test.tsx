// ChatShell — the composer wraps long text (2026-08-06 ONE UAT regression).
//
// The composer was a single-line `<input>`. An `<input>` CANNOT wrap: a long
// prompt scrolls sideways and the user cannot see what they typed. Two users
// reported it independently as their top complaint ("it never breaks the line,
// it keeps on writing horizontally" / "impossible to see the whole prompt"),
// on desktop web, not just mobile.
//
// The fix is a `<textarea>`, which wraps natively. That trade brings its own
// hazard: a textarea treats Enter as "newline", so without an explicit keydown
// handler the send key silently stops working — swapping a visible bug for a
// worse invisible one. Both halves are pinned here.
//
// SCOPE / HONESTY: jsdom does no layout, so this cannot prove pixels wrap. What
// it DOES prove is the property that makes wrapping possible (the control is a
// textarea, not an input, and isn't forced to one line) plus the full
// Enter/Shift+Enter contract. Visual confirmation is a browser job.

import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { UseSkillAgentReturn } from "@/hooks/useSkillAgent";

function makeReturn(overrides: Partial<UseSkillAgentReturn> = {}): UseSkillAgentReturn {
  return {
    sessionId: "test-thread",
    messages: [],
    toolCalls: [],
    thinkingContent: "",
    isThinking: false,
    stageLabel: null,
    delegations: [],
    fallbacks: [],
    compactions: [],
    tidyingUp: false,
    resolvedModel: null,
    sendMessage: vi.fn().mockResolvedValue(undefined),
    isLoading: false,
    error: null,
    clearError: vi.fn(),
    stop: vi.fn(),
    ...overrides,
  };
}

vi.mock("@/hooks/useSkillAgent", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/hooks/useSkillAgent")>();
  return { ...mod, useSkillAgent: vi.fn() };
});

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { uid: "test" }, loading: false }),
}));

type CustomEventHandler = (e: { event: { name?: unknown; value?: unknown } }) => void;
const subscribers: CustomEventHandler[] = [];

vi.mock("@/providers/AGUIProvider", () => ({
  AGUIProvider: ({ children }: { children: React.ReactNode }) => children,
  useAGUIAgent: () => ({
    subscribe: ({ onCustomEvent }: { onCustomEvent?: CustomEventHandler }) => {
      if (onCustomEvent) subscribers.push(onCustomEvent);
      return { unsubscribe: () => {} };
    },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => ({ get: vi.fn().mockReturnValue(null) }),
}));

vi.mock("@/hooks/useSlugResolution", () => ({
  useSlugResolution: () => ({ skillId: "test-skill-id", loading: false, notFound: false, error: null }),
}));

vi.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => ({ ready: true, lastError: null }),
}));

vi.mock("@/hooks/useSkillMeta", () => ({
  useSkillMeta: () => ({
    displayName: "Test Skill",
    ownerId: "test-owner",
    slug: "test-slug",
    mcpServerIds: [],
    initialMessage: "",
    welcome: null,
    model: "",
    voice: null,
    a2ui: { allowActionTriggeredRuns: false },
    loading: false,
  }),
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";
import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

async function renderChat() {
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  await waitFor(() => expect(subscribers.length).toBeGreaterThan(0));
}

/** The composer control, found the way a user finds it — by its placeholder. */
function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText("Message…") as HTMLTextAreaElement;
}

/** A prompt long enough that it could only ever be read if it wraps — the
 * shape of the real ONE prompts that triggered the report. */
const LONG_PROMPT =
  "Compare the termination clauses in these two PPA contracts and tell me " +
  "which one is more favourable to the offtaker, with reference to the " +
  "change-in-law provisions and the settlement timeline.";

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn());
});

describe("ChatShell composer — wrapping (2026-08-06 ONE UAT)", () => {
  it("is a textarea, not a single-line input", async () => {
    await renderChat();
    const el = composer();

    // The regression itself. An <input> cannot wrap at any CSS setting, so
    // this tag check IS the fix — not a proxy for it.
    expect(el.tagName).toBe("TEXTAREA");
  });

  it("does not suppress wrapping via rows/wrap attributes", async () => {
    await renderChat();
    const el = composer();

    // A textarea can still be forced to one line: wrap="off" reintroduces
    // exactly the horizontal-scroll bug we just fixed.
    expect(el.getAttribute("wrap")).not.toBe("off");
    // Resting height is one row (auto-grow handles the rest) — a taller
    // default would eat chat space for the common short message.
    expect(el.rows).toBe(1);
  });

  it("holds a long multi-line value without truncating it", async () => {
    await renderChat();
    const el = composer();

    fireEvent.change(el, { target: { value: LONG_PROMPT } });
    expect(el.value).toBe(LONG_PROMPT);

    // Explicit newlines survive too — an <input> would have dropped them.
    fireEvent.change(el, { target: { value: "line one\nline two" } });
    expect(el.value).toBe("line one\nline two");
  });

  it("sends on Enter", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
    await renderChat();
    const el = composer();

    fireEvent.change(el, { target: { value: LONG_PROMPT } });
    fireEvent.keyDown(el, { key: "Enter" });

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(sendMessage.mock.calls[0][0]).toBe(LONG_PROMPT);
  });

  it("inserts a newline on Shift+Enter instead of sending", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
    await renderChat();
    const el = composer();

    fireEvent.change(el, { target: { value: "first line" } });
    fireEvent.keyDown(el, { key: "Enter", shiftKey: true });

    expect(sendMessage).not.toHaveBeenCalled();
    // Not preventDefault'd, so the browser's own newline insertion still runs.
    expect(el.value).toBe("first line");
  });

  it("does not send on Enter while an IME composition is open", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
    await renderChat();
    const el = composer();

    fireEvent.change(el, { target: { value: "spanish text" } });
    // Enter commits the candidate for CJK/accented input; treating it as
    // "send" would fire a half-typed message. ONE's users type Spanish.
    fireEvent.keyDown(el, { key: "Enter", isComposing: true });

    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("still refuses to send an empty or whitespace-only draft on Enter", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
    await renderChat();
    const el = composer();

    fireEvent.keyDown(el, { key: "Enter" });
    fireEvent.change(el, { target: { value: "   \n  " } });
    fireEvent.keyDown(el, { key: "Enter" });

    expect(sendMessage).not.toHaveBeenCalled();
  });
});
