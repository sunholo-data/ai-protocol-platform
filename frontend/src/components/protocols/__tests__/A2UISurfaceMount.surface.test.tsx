// A2UISurfaceMount — v0.9 surface rendering tests
//
// After the v0.9 rewrite, the mount reads the SurfaceModel from
// SurfaceRegistry state and renders <A2uiSurface surface={...}>. These
// tests cover:
//   - No state → mount div renders empty (no <A2uiSurface>)
//   - After appendMessages writes a surface → <A2uiSurface> renders inside
//   - clearSurface → <A2uiSurface> unmounts
//   - Different-surface messages don't bleed in

import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { A2UISurfaceMount } from "../A2UISurfaceMount";
import { logger } from "@/lib/logger";
import { subscribeWorkbenchConfirmation } from "@/lib/workbenchConfirmation";
import { subscribeWorkbenchFocus } from "@/lib/workbenchFocus";
import {
  SurfaceRegistryProvider,
  useSurfaceRegistry,
} from "@/providers/SurfaceRegistry";

// ─── v0.9 SDK doubles (inlined inside vi.mock factory — TDZ-safe) ──────────

vi.mock("@a2ui/web_core/v0_9", () => {
  type ActionHandler = (action: unknown) => void;
  class FakeProcessor {
    private surfaces = new Map<string, {
      id: string;
      catalog: { id: string };
      dataModel: { lastValue: unknown; get: () => undefined; subscribe: () => { unsubscribe: () => void } };
      onAction: { subscribe: (h: ActionHandler) => { unsubscribe: () => void } };
      // Test-only escape hatch — sprint 2.10 mount subscribes to
      // surface.onAction; tests use this to fire a synthetic action
      // and assert the POST shape.
      _fireAction: (action: unknown) => void;
      dispose: () => void;
    }>();
    readonly model = {
      getSurface: (id: string) => this.surfaces.get(id),
      onAction: { subscribe: () => ({ unsubscribe: () => {} }) },
    };

    processMessages(messages: Array<Record<string, unknown>>) {
      for (const msg of messages) {
        if (msg.createSurface) {
          const p = msg.createSurface as { surfaceId: string; catalogId: string };
          if (this.surfaces.has(p.surfaceId))
            throw new Error(`Surface ${p.surfaceId} already exists.`);
          const handlers = new Set<ActionHandler>();
          this.surfaces.set(p.surfaceId, {
            id: p.surfaceId,
            catalog: { id: p.catalogId },
            dataModel: { lastValue: undefined, get: () => undefined, subscribe: () => ({ unsubscribe: () => {} }) },
            onAction: {
              subscribe: (h: ActionHandler) => {
                handlers.add(h);
                return { unsubscribe: () => handlers.delete(h) };
              },
            },
            _fireAction: (action: unknown) => {
              for (const h of handlers) h(action);
            },
            dispose: () => {},
          });
        } else if (msg.deleteSurface) {
          const p = msg.deleteSurface as { surfaceId: string };
          this.surfaces.delete(p.surfaceId);
        }
      }
    }
  }
  return { MessageProcessor: FakeProcessor };
});

vi.mock("@/components/protocols/workbenchCatalog", () => ({ workbenchCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" } }));

vi.mock("@a2ui/react/v0_9", () => ({
  basicCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" },
  A2uiSurface: ({ surface }: { surface: { id: string } }) => (
    <div data-testid="a2ui-surface" data-surface-id={surface.id} />
  ),
}));

// The markdown catalog is Basic with one component swapped, built at module
// load from `createReactComponent` + `basicCatalog.components`. These suites
// stub the SDK to avoid rendering it, so the real builder would explode on the
// stub — and it has nothing to do with what is under test here. Stubbed to the
// only field this code path reads.
vi.mock("@/components/protocols/a2uiMarkdownCatalog", () => ({
  markdownCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" },
}));

// Sprint 2.10 — mount POSTs A2uiClientAction to the backend. Mock
// fetchWithAuth so we can assert URL + body without a real fetch.
const fetchWithAuthMock = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuthMock(...args),
}));

// ─── Helpers ────────────────────────────────────────────────────────────────

const CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json";

function createMsg(surfaceId: string) {
  return {
    version: "v0.9",
    createSurface: { surfaceId, catalogId: CATALOG_ID },
  };
}

let capturedRegistry: ReturnType<typeof useSurfaceRegistry> | null = null;
function CaptureRegistry() {
  capturedRegistry = useSurfaceRegistry();
  return null;
}

function renderMount(props: Parameters<typeof A2UISurfaceMount>[0]) {
  capturedRegistry = null;
  return render(
    <SurfaceRegistryProvider>
      <CaptureRegistry />
      <A2UISurfaceMount {...props} />
    </SurfaceRegistryProvider>,
  );
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("A2UISurfaceMount — v0.9 surface rendering", () => {
  it("renders the mount div with no <A2uiSurface> child when surface is empty", () => {
    const { container } = renderMount({ surfaceId: "workspace" });
    const mountDiv = container.querySelector("[data-surface='workspace']");
    expect(mountDiv).toBeInTheDocument();
    expect(screen.queryByTestId("a2ui-surface")).not.toBeInTheDocument();
  });

  it("renders <A2uiSurface> inside the mount after appendMessages with createSurface", () => {
    renderMount({ surfaceId: "workspace" });
    expect(screen.queryByTestId("a2ui-surface")).not.toBeInTheDocument();

    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });

    const rendered = screen.getByTestId("a2ui-surface");
    expect(rendered).toBeInTheDocument();
    expect(rendered.getAttribute("data-surface-id")).toBe("workspace");
  });

  it("unmounts <A2uiSurface> after clearSurface", () => {
    renderMount({ surfaceId: "workspace" });
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    expect(screen.getByTestId("a2ui-surface")).toBeInTheDocument();

    act(() => {
      capturedRegistry!.clearSurface("workspace");
    });
    expect(screen.queryByTestId("a2ui-surface")).not.toBeInTheDocument();
  });

  it("does not render surfaces from a different surfaceId", () => {
    renderMount({ surfaceId: "workspace" });
    act(() => {
      capturedRegistry!.appendMessages(
        "sidebar",
        [createMsg("sidebar")],
        "tc-1",
      );
    });
    expect(screen.queryByTestId("a2ui-surface")).not.toBeInTheDocument();
  });
});

// Sprint 2.10 — surface.onAction → POST /api/sessions/{id}/surface-action
describe("A2UISurfaceMount — onAction dispatch (sprint 2.10)", () => {
  function fireAction(action: unknown) {
    // Reach into the FakeProcessor surface (mock has a _fireAction hook).
    const surface = capturedRegistry!.getState("workspace")?.surface as
      | { _fireAction: (a: unknown) => void }
      | undefined;
    if (!surface) throw new Error("workspace surface not created");
    act(() => {
      surface._fireAction(action);
    });
  }

  beforeEach(() => {
    fetchWithAuthMock.mockReset();
    fetchWithAuthMock.mockResolvedValue({
      ok: true,
      status: 204,
      text: async () => "",
    });
  });

  it("POSTs the action to /api/sessions/{id}/surface-action with the right shape", async () => {
    renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    fireAction({
      name: "approve",
      sourceComponentId: "row-47",
      timestamp: "2026-05-18T18:00:00Z",
      context: { id: 47 },
    });

    // Wait one microtask for the async POST.
    await act(async () => {});

    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchWithAuthMock.mock.calls[0];
    expect(url).toBe("/api/proxy/api/sessions/sess-1/surface-action");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      surfaceId: "workspace",
      action: {
        name: "approve",
        sourceComponentId: "row-47",
        timestamp: "2026-05-18T18:00:00Z",
        context: { id: 47 },
      },
    });
  });

  it("does NOT POST when sessionId is null", async () => {
    renderMount({ surfaceId: "workspace", sessionId: null });
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    // We can't fire an action because the subscription was skipped at
    // mount time (effect bails when sessionId is null). Assert no POST.
    await act(async () => {});
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it("swallows 403 responses without breaking the mount (skill not opted in)", async () => {
    fetchWithAuthMock.mockResolvedValue({
      ok: false,
      status: 403,
      text: async () => "Skill not opted into surface context writes",
    });
    const consoleInfo = vi.spyOn(console, "info").mockImplementation(() => {});

    renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    fireAction({ name: "click" });
    await act(async () => {});

    // POST happened…
    expect(fetchWithAuthMock).toHaveBeenCalled();
    // …surface still rendered
    expect(screen.getByTestId("a2ui-surface")).toBeInTheDocument();
    consoleInfo.mockRestore();
  });

  it("swallows network errors without breaking the mount", async () => {
    fetchWithAuthMock.mockRejectedValue(new Error("network down"));
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});

    renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    fireAction({ name: "click" });
    await act(async () => {});

    expect(screen.getByTestId("a2ui-surface")).toBeInTheDocument();
    consoleWarn.mockRestore();
  });
});

// Two-way workbench mode C — direct:<name> → POST surface-action-direct, then a
// status bar pinned to the top of the surface (NEVER SILENT).
describe("A2UISurfaceMount — direct actions", () => {
  function setup() {
    renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
    act(() => {
      capturedRegistry!.appendMessages("workspace", [createMsg("workspace")], "tc-1");
    });
    const surface = capturedRegistry!.getState("workspace")?.surface as unknown as {
      _fireAction: (a: unknown) => void;
    };
    return (action: unknown) =>
      act(() => {
        surface._fireAction(action);
      });
  }

  beforeEach(() => fetchWithAuthMock.mockReset());

  it("POSTs to surface-action-direct and shows the server's message", async () => {
    const replies: unknown[] = [];
    const unsubscribe = subscribeWorkbenchConfirmation(reply => replies.push(reply));
    fetchWithAuthMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", message: "Marked 2 read (#1, #3).", messages: [], confirmation: { id: "reply-1", content: "Marked two messages read." } }),
    });
    const fire = setup();
    fire({ name: "direct:mark_read", context: { selected: { m1: true, m3: true } } });
    await act(async () => {});

    const [url, init] = fetchWithAuthMock.mock.calls[0];
    expect(url).toBe("/api/proxy/api/sessions/sess-1/surface-action-direct");
    expect(JSON.parse(init.body as string).action.context).toEqual({ selected: { m1: true, m3: true } });
    const bar = screen.getByTestId("a2ui-surface-direct-status");
    expect(bar).toHaveAttribute("data-kind", "ok");
    expect(bar).toHaveTextContent("Marked 2 read (#1, #3).");
    expect(replies).toEqual([{ sessionId: "sess-1", id: "reply-1", content: "Marked two messages read." }]);
    unsubscribe();
  });

  it("shows a refusal or handler error as a visible alert", async () => {
    fetchWithAuthMock.mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: "This skill can't do that" }),
    });
    const fire = setup();
    fire({ name: "direct:mark_read", context: {} });
    await act(async () => {});
    const bar = screen.getByTestId("a2ui-surface-direct-status");
    expect(bar).toHaveAttribute("role", "alert");
    expect(bar).toHaveTextContent("This skill can't do that");

    fetchWithAuthMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "error", message: "Tick one or more messages first.", messages: [] }),
    });
    fire({ name: "direct:mark_read", context: {} });
    await act(async () => {});
    expect(screen.getByTestId("a2ui-surface-direct-status")).toHaveTextContent("Tick one or more");
  });

  it("reports every failed action to the client log, not only the status bar", async () => {
    const warn = vi.spyOn(logger, "warn").mockImplementation(() => {});
    const fire = setup();

    fetchWithAuthMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: "Service is down." }) });
    fire({ name: "direct:refresh", context: {} });
    await act(async () => {});
    fetchWithAuthMock.mockResolvedValue({
      ok: true, status: 200, json: async () => ({ status: "error", message: "This request changed.", messages: [] }),
    });
    fire({ name: "direct:approve", context: {} });
    await act(async () => {});
    fetchWithAuthMock.mockRejectedValue(new TypeError("Load failed"));
    fire({ name: "direct:history", context: {} });
    await act(async () => {});
    fetchWithAuthMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: "ok", messages: [] }) });
    fire({ name: "direct:pending", context: {} });
    await act(async () => {});

    const reports = warn.mock.calls.filter(([name]) => name === "surface_direct_action_failed");
    expect(reports.map(([, attrs]) => attrs)).toEqual([
      expect.objectContaining({ kind: "direct:refresh", status: 503, errorMessage: "Service is down." }),
      expect.objectContaining({ kind: "direct:approve", status: 200, errorMessage: "This request changed." }),
      expect.objectContaining({ kind: "direct:history", errorName: "TypeError", errorMessage: "Load failed" }),
    ]);
    warn.mockRestore();
  });
});

it("direct review registers a private display-only surface and never exports it", async () => {
  const id = "private:workspace:review";
  fetchWithAuthMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({
    status: "ok", message: "Review opened.", messages: [], private_surfaces: [{
      surface_id: id, messages: [createMsg(id)], artifact: { kind: "review", title: "Review" },
    }],
  }) });
  renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
  act(() => capturedRegistry!.appendMessages("workspace", [createMsg("workspace")], "control"));
  const surface = capturedRegistry!.getState("workspace")?.surface as unknown as { _fireAction: (a: unknown) => void };
  act(() => surface._fireAction({ name: "direct:review", context: { id: "safe-id" } }));
  await act(async () => {});
  expect(capturedRegistry!.getState(id)?.surface).toBeTruthy();
  expect(capturedRegistry!.readA2uiSurfaceState()).not.toHaveProperty(id);
  expect(capturedRegistry!.getPolicy(id).modelExport).toBe(false);
});

it("private display surfaces never dispatch action context back to the server", async () => {
  const id = "private:workspace:review";
  fetchWithAuthMock.mockReset();
  renderMount({ surfaceId: id, sessionId: "sess-1", onChatMessage: vi.fn() });
  act(() => capturedRegistry!.appendPrivateMessages(id, [createMsg(id)], "review"));
  const surface = capturedRegistry!.getState(id)?.surface as unknown as { _fireAction: (a: unknown) => void };
  act(() => surface._fireAction({ name: "direct:approve", context: { purpose: "canary" } }));
  await act(async () => {});
  expect(fetchWithAuthMock).not.toHaveBeenCalled();
});

it("a private panel's focus: button switches tabs on the client and sends nothing", async () => {
  const id = "private:daneel_approvals:review";
  fetchWithAuthMock.mockReset();
  const focused: string[] = [];
  const unsubscribe = subscribeWorkbenchFocus((target) => focused.push(target));
  renderMount({ surfaceId: id, sessionId: "sess-1", onChatMessage: vi.fn() });
  act(() => capturedRegistry!.appendPrivateMessages(id, [createMsg(id)], "review"));
  const surface = capturedRegistry!.getState(id)?.surface as unknown as { _fireAction: (a: unknown) => void };
  act(() => surface._fireAction({ name: "focus:daneel_approvals", context: { request_id: "" } }));
  act(() => surface._fireAction({ name: "direct:approve", context: {} }));
  await act(async () => {});
  unsubscribe();
  expect(focused).toEqual(["daneel_approvals"]);
  expect(fetchWithAuthMock).not.toHaveBeenCalled();
});

it("late private review responses cannot resurrect content after session switch", async () => {
  let resolve!: (value: unknown) => void;
  fetchWithAuthMock.mockReturnValue(new Promise(r => { resolve = r; }));
  const view = renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
  act(() => capturedRegistry!.appendMessages("workspace", [createMsg("workspace")], "control"));
  const surface = capturedRegistry!.getState("workspace")?.surface as unknown as { _fireAction: (a: unknown) => void };
  act(() => surface._fireAction({ name: "direct:review" }));
  const registry = capturedRegistry!;
  view.rerender(<SurfaceRegistryProvider><CaptureRegistry /><A2UISurfaceMount surfaceId="workspace" sessionId="sess-2" /></SurfaceRegistryProvider>);
  const id = "private:workspace:review";
  await act(async () => resolve({ ok: true, status: 200, json: async () => ({
    status: "ok", private_surfaces: [{ surface_id: id, messages: [createMsg(id)] }],
  }) }));
  expect(registry.getState(id)).toBeNull();
});

describe("A2UISurfaceMount — automatic status reads", () => {
  function seed() {
    return renderMount({ surfaceId: "workspace", sessionId: "sess-1" });
  }
  function append() {
    act(() => capturedRegistry!.appendMessages("workspace", [createMsg("workspace")], "auto-board", {
      kind: "test_board", autoRefresh: { action: "direct:read_status", intervalSeconds: 15 },
    }));
  }
  it("refreshes visible opted-in surfaces without a click and stops on unmount", async () => {
    vi.useFakeTimers();
    const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const rects = vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
    fetchWithAuthMock.mockReset().mockResolvedValue({ ok: true, json: async () => ({ status: "ok", messages: [] }) });
    const view = seed();
    try {
      append(); await act(async () => {});
      expect(JSON.parse(fetchWithAuthMock.mock.calls[0][1].body)).toEqual({
        surfaceId: "workspace", automatic: true, action: { name: "direct:read_status", context: {} },
      });
      expect(screen.queryByTestId("a2ui-surface-direct-status")).toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
      expect(fetchWithAuthMock).toHaveBeenCalledTimes(2);
      visible.mockReturnValue("hidden");
      await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
      expect(fetchWithAuthMock).toHaveBeenCalledTimes(2);
      view.unmount();
      await vi.advanceTimersByTimeAsync(15000);
      expect(fetchWithAuthMock).toHaveBeenCalledTimes(2);
    } finally { view.unmount(); visible.mockRestore(); rects.mockRestore(); vi.useRealTimers(); }
  });
  it("does not overwrite a newer human action with an older automatic response", async () => {
    const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const rects = vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
    let resolve!: (value: unknown) => void;
    fetchWithAuthMock.mockReset().mockReturnValueOnce(new Promise(r => { resolve = r; }))
      .mockResolvedValue({ ok: true, json: async () => ({ status: "ok", messages: [] }) });
    const view = seed();
    try {
      append();
      const appendSpy = vi.spyOn(capturedRegistry!, "appendMessages");
      const surface = capturedRegistry!.getState("workspace")?.surface as unknown as { _fireAction: (a: unknown) => void };
      await act(async () => { surface._fireAction({ name: "direct:approve", context: {} }); });
      await act(async () => resolve({ ok: true, json: async () => ({ status: "ok", messages: [{ updateDataModel: { surfaceId: "workspace", value: { stale: true } } }] }) }));
      expect(appendSpy).not.toHaveBeenCalled();
      appendSpy.mockRestore();
    } finally { view.unmount(); visible.mockRestore(); rects.mockRestore(); }
  });
});
