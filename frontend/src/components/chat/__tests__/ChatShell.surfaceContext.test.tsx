// Default ChatShell: real registry, hook and A2UI SDK; AG-UI transport is a test double.

import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
// Keep the real hook and registry: the regression was between their providers.
type Subscriber = Record<string, (event: any) => void>;
class FakeAgent {
  threadId = "selection-thread";
  messages: any[] = [];
  subscribers = new Set<Subscriber>();
  subscribe(subscriber: Subscriber) {
    this.subscribers.add(subscriber);
    return { unsubscribe: () => this.subscribers.delete(subscriber) };
  }
  emit(name: string, value: unknown) {
    for (const subscriber of this.subscribers) subscriber[name]?.(value);
  }
  addMessage(message: unknown) {
    this.messages.push(message);
    this.emit("onMessagesChanged", {});
  }
  runAgent = vi.fn(async (_input?: unknown) => {
    this.emit("onRunStartedEvent", {});
    this.emit("onRunFinishedEvent", { event: {} });
    this.emit("onRunFinalized", {});
    return { newMessages: [] };
  });
  abortRun = vi.fn();
}
const agent = new FakeAgent();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { uid: "test" }, loading: false }),
}));

vi.mock("@/providers/AGUIProvider", () => ({
  AGUIProvider: ({ children }: { children: React.ReactNode }) => children,
  useAGUIAgent: () => agent,
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

import inboxSurface from "./inbox-surface.json";

import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

async function renderChat() {
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  await waitFor(() => expect(agent.subscribers.size).toBeGreaterThan(0));
}

/** The composer control, found the way a user finds it — by its placeholder. */
function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText(/^Message .*…$/) as HTMLTextAreaElement;
}

const surfaceId = "inbox-result";
const catalogId = "https://a2ui.org/specification/v0_9/basic_catalog.json";

function showBoard(selected: number[]) {
  act(() => agent.emit("onCustomEvent", { event: {
    name: "A2UI_SURFACE",
    value: {
      surfaceId,
      sourceId: `listing-${selected.join("-")}`,
      artifact: { kind: "inbox_board", title: "Inbox" },
      messages: [
        { version: "v0.9", createSurface: { surfaceId, catalogId } },
        { version: "v0.9", updateComponents: { surfaceId, components: [
          { id: "root", component: "Text", text: "Inbox" },
        ] } },
        { version: "v0.9", updateDataModel: { surfaceId, value: {
          selected, filter: "unread", items: [{ ref: 2, title: "Review" }, { ref: 5, title: "Approve" }],
        } } },
      ],
    },
  } }));
}

async function send() {
  fireEvent.change(composer(), { target: { value: "Draft replies to these" } });
  fireEvent.keyDown(composer(), { key: "Enter" });
  await waitFor(() => expect(agent.runAgent).toHaveBeenCalled());
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  agent.messages = [];
  agent.subscribers.clear();
  agent.runAgent.mockClear();
});

describe("ChatShell workbench → chat context", () => {
  it("sends result-mapped surface selection and view state through the real hook", async () => {
    await renderChat();
    showBoard([2, 5]);
    await screen.findByRole("tab", { name: /Inbox/i });
    await send();
    expect(agent.runAgent).toHaveBeenLastCalledWith({ forwardedProps: {
      a2ui_surface_state: { [surfaceId]: { catalogId, dataModel: {
        selected: [2, 5], filter: "unread", items: [{ ref: 2, title: "Review" }, { ref: 5, title: "Approve" }],
      } } },
    } });

    agent.runAgent.mockClear();
    showBoard([2]);
    await send();
    expect(agent.runAgent).toHaveBeenLastCalledWith({ forwardedProps: {
      a2ui_surface_state: { [surfaceId]: { catalogId, dataModel: {
        selected: [2], filter: "unread", items: [{ ref: 2, title: "Review" }, { ref: 5, title: "Approve" }],
      } } },
    } });
  });

  it("carries checkbox edits from a captured real ADK/AGUI inbox stream into the next turn", async () => {
    await renderChat();
    act(() => agent.emit("onCustomEvent", { event: { name: "A2UI_SURFACE", value: structuredClone(inboxSurface) } }));
    await screen.findByRole("tab", { name: /Inbox/i });
    fireEvent.click(await screen.findByRole("checkbox", { name: "1. Review plan" }));
    await send();
    const input = agent.runAgent.mock.calls.at(-1)?.[0] as any;
    expect(input.forwardedProps.a2ui_surface_state.inbox_board.dataModel.selected).toEqual({ "1": true });
    expect(input.forwardedProps.a2ui_surface_state.inbox_board.dataModel.items[0].title).toBe("Review plan");
  });

  it("omits surface context when no surface is displayed", async () => {
    await renderChat();
    await send();
    expect(agent.runAgent).toHaveBeenLastCalledWith(undefined);
  });
});
