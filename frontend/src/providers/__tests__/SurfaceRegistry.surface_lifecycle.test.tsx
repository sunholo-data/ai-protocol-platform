// SurfaceRegistry — v0.9 lifecycle tests
//
// Replaces the M4 "patch" tests. In v0.9 there is no separate patch API —
// the message stream itself encodes intent (updateComponents replaces the
// component tree, updateDataModel mutates the data model). The registry's
// job is to feed messages to the per-surface MessageProcessor and surface
// the resulting SurfaceModel via useSurfaceState.
//
// Covers:
//   1. updateDataModel after createSurface mutates the SurfaceModel's
//      dataModel without recreating the surface (identity preserved).
//   2. `clearByPersistence("session-scoped")` clears workspace/sidebar but
//      NOT chat (turn-scoped) or modal (turn-scoped).
//   3. `useClearSurfacesOnSessionChange` clears session-scoped surfaces
//      on sessionId change; no-op on initial null→id transition.

import { act, render, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  SurfaceRegistryProvider,
  useClearSurfacesOnSessionChange,
  useSurfaceRegistry,
  useSurfaceState,
} from "../SurfaceRegistry";

// ─── v0.9 SDK doubles (inlined inside vi.mock factory — TDZ-safe) ──────────

vi.mock("@a2ui/web_core/v0_9", () => {
  class FakeProcessor {
    private surfaces = new Map<string, {
      id: string;
      catalog: { id: string };
      dataModel: { lastValue: unknown };
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
          this.surfaces.set(p.surfaceId, {
            id: p.surfaceId,
            catalog: { id: p.catalogId },
            dataModel: { lastValue: undefined },
            dispose: () => {},
          });
        } else if (msg.updateDataModel) {
          const p = msg.updateDataModel as { surfaceId: string; value: unknown };
          const s = this.surfaces.get(p.surfaceId);
          if (!s) throw new Error(`Surface not found: ${p.surfaceId}`);
          s.dataModel.lastValue = p.value;
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
}));

// ─── Helpers ────────────────────────────────────────────────────────────────

const CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json";

function createMsg(surfaceId: string) {
  return {
    version: "v0.9",
    createSurface: { surfaceId, catalogId: CATALOG_ID },
  };
}

function updateData(surfaceId: string, value: Record<string, unknown>) {
  return {
    version: "v0.9",
    updateDataModel: { surfaceId, value },
  };
}

let capturedRegistry: ReturnType<typeof useSurfaceRegistry> | null = null;
function CaptureRegistry() {
  capturedRegistry = useSurfaceRegistry();
  return null;
}

function withProvider(child: React.ReactNode) {
  return (
    <SurfaceRegistryProvider>
      <CaptureRegistry />
      {child}
    </SurfaceRegistryProvider>
  );
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("SurfaceRegistry — v0.9 lifecycle", () => {
  it("updateDataModel after createSurface preserves the SurfaceModel identity", () => {
    render(withProvider(null));
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    const surfaceBefore = capturedRegistry!.getState("workspace")?.surface;
    expect(surfaceBefore).toBeDefined();

    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [updateData("workspace", { zoom: 15 })],
        "tc-2",
      );
    });
    const surfaceAfter = capturedRegistry!.getState("workspace")?.surface;
    // Same SurfaceModel reference — React's reconciler will NOT remount
    // <A2uiSurface surface={...}>. This is the "keeps stateful widgets
    // alive across data updates" property.
    expect(surfaceAfter).toBe(surfaceBefore);
  });

  it("auto-creates surface when first message is an updateDataModel (no prior createSurface)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(withProvider(null));
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [updateData("workspace", { initialized: true })],
        "tc-bootstrap",
      );
    });
    const state = capturedRegistry!.getState("workspace");
    expect(state?.surface?.id).toBe("workspace");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("SurfaceRegistry — clearByPersistence", () => {
  it("clears session-scoped surfaces but not turn-scoped surfaces", () => {
    render(withProvider(null));
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-ws",
      );
      capturedRegistry!.appendMessages(
        "sidebar",
        [createMsg("sidebar")],
        "tc-sb",
      );
      capturedRegistry!.appendMessages(
        "chat",
        [createMsg("chat")],
        "tc-chat",
      );
      capturedRegistry!.appendMessages(
        "modal",
        [createMsg("modal")],
        "tc-modal",
      );
    });

    expect(capturedRegistry!.getState("workspace")?.surface).toBeDefined();
    expect(capturedRegistry!.getState("chat")?.surface).toBeDefined();

    let cleared = 0;
    act(() => {
      cleared = capturedRegistry!.clearByPersistence("session-scoped");
    });

    expect(cleared).toBe(2);
    expect(capturedRegistry!.getState("workspace")).toBeNull();
    expect(capturedRegistry!.getState("sidebar")).toBeNull();
    expect(capturedRegistry!.getState("chat")?.surface).toBeDefined();
    expect(capturedRegistry!.getState("modal")?.surface).toBeDefined();
  });

  it("is idempotent — re-running returns 0", () => {
    render(withProvider(null));
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
      capturedRegistry!.clearByPersistence("session-scoped");
    });
    let cleared = -1;
    act(() => {
      cleared = capturedRegistry!.clearByPersistence("session-scoped");
    });
    expect(cleared).toBe(0);
  });
});

describe("useClearSurfacesOnSessionChange", () => {
  it("does NOT clear on initial null-to-id transition", () => {
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => {
        useClearSurfacesOnSessionChange(id);
      },
      {
        initialProps: { id: null as string | null },
        wrapper: ({ children }) => (
          <SurfaceRegistryProvider>
            <CaptureRegistry />
            {children}
          </SurfaceRegistryProvider>
        ),
      },
    );
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    rerender({ id: "session-A" });
    expect(capturedRegistry!.getState("workspace")?.surface).toBeDefined();
  });

  it("clears BOTH session-scoped and turn-scoped surfaces when sessionId transitions A → B", () => {
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => {
        useClearSurfacesOnSessionChange(id);
      },
      {
        initialProps: { id: "session-A" as string | null },
        wrapper: ({ children }) => (
          <SurfaceRegistryProvider>
            <CaptureRegistry />
            {children}
          </SurfaceRegistryProvider>
        ),
      },
    );
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
      capturedRegistry!.appendMessages(
        "chat",
        [createMsg("chat")],
        "tc-chat",
      );
    });

    rerender({ id: "session-B" });

    // A new conversation wipes BOTH session-scoped (workspace) AND turn-scoped
    // (chat — the fallback policy for backend-pushed elicitation / handoff-confirm
    // cards). Clearing only session-scoped leaked the previous session's confirm
    // card into the fresh conversation (2026-07-14 report).
    expect(capturedRegistry!.getState("workspace")).toBeNull();
    expect(capturedRegistry!.getState("chat")).toBeNull();
  });

  it("re-renders subscribers when their surface is cleared by lifecycle", () => {
    const renders: Array<string | null> = [];
    function Subscriber() {
      const state = useSurfaceState("workspace");
      renders.push(state?.surface?.id ?? null);
      return null;
    }
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => {
        useClearSurfacesOnSessionChange(id);
      },
      {
        initialProps: { id: "session-A" as string | null },
        wrapper: ({ children }) => (
          <SurfaceRegistryProvider>
            <CaptureRegistry />
            <Subscriber />
            {children}
          </SurfaceRegistryProvider>
        ),
      },
    );
    act(() => {
      capturedRegistry!.appendMessages(
        "workspace",
        [createMsg("workspace")],
        "tc-1",
      );
    });
    expect(renders.at(-1)).toBe("workspace");

    rerender({ id: "session-B" });
    expect(renders.at(-1)).toBeNull();
  });
});
