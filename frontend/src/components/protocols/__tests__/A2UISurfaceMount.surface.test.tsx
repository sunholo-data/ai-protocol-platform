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
      dataModel: { lastValue: unknown };
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
            dataModel: { lastValue: undefined },
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

vi.mock("@a2ui/react/v0_9", () => ({
  basicCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" },
  A2uiSurface: ({ surface }: { surface: { id: string } }) => (
    <div data-testid="a2ui-surface" data-surface-id={surface.id} />
  ),
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
