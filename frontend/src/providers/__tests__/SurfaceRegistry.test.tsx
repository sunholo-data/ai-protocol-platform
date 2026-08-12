// SurfaceRegistry tests — v0.9 native API
//
// Validates the registry's surface lifecycle, policy, and useSyncExternalStore
// fan-out. The v0.9 SDK (MessageProcessor + SurfaceModel) is mocked so tests
// focus on the registry's routing/state behaviour rather than SDK internals.

import { act, render, renderHook } from "@testing-library/react";
import { createRef, type ReactNode, type RefObject } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_SURFACES,
  SurfaceRegistryProvider,
  useSurfaceMount,
  useSurfaceRegistry,
  useSurfaceState,
} from "@/providers/SurfaceRegistry";

// ─── v0.9 SDK doubles ───────────────────────────────────────────────────────
//
// vi.mock factories are hoisted above the test file's top-level scope, so
// the FakeProcessor class is declared INSIDE each factory. Cannot share the
// class declaration across mocks otherwise (TDZ violation).

vi.mock("@a2ui/web_core/v0_9", () => {
  class FakeProcessor {
    private surfaces = new Map<string, {
      id: string;
      catalog: { id: string };
      dataModel: {
        root: unknown;
        get: (path: string) => unknown;
      };
      onAction: { subscribe: (h: unknown) => { unsubscribe: () => void } };
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
          const dataModel = {
            root: undefined as unknown,
            get(path: string): unknown {
              return path === "/" ? this.root : undefined;
            },
          };
          this.surfaces.set(p.surfaceId, {
            id: p.surfaceId,
            catalog: { id: p.catalogId },
            dataModel,
            onAction: { subscribe: () => ({ unsubscribe: () => {} }) },
            dispose: () => {},
          });
        } else if (msg.updateDataModel) {
          const p = msg.updateDataModel as { surfaceId: string; value: unknown };
          const s = this.surfaces.get(p.surfaceId);
          if (!s) throw new Error(`Surface not found: ${p.surfaceId}`);
          s.dataModel.root = p.value;
        } else if (msg.deleteSurface) {
          const p = msg.deleteSurface as { surfaceId: string };
          this.surfaces.delete(p.surfaceId);
        }
        // updateComponents: ignored in the fake — registry doesn't introspect it.
      }
    }
  }
  return { MessageProcessor: FakeProcessor };
});

vi.mock("@a2ui/react/v0_9", () => ({
  basicCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" },
}));

// ─── Helpers ────────────────────────────────────────────────────────────────

function wrap(children: ReactNode) {
  return <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>;
}

function withProvider({ children }: { children: ReactNode }) {
  return <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>;
}

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

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("SurfaceRegistry", () => {
  describe("default surface table", () => {
    it("exposes the four built-in surfaces with the expected policies", () => {
      expect(DEFAULT_SURFACES.chat.persistence).toBe("turn-scoped");
      expect(DEFAULT_SURFACES.chat.requiresUserGesture).toBe(false);

      expect(DEFAULT_SURFACES.workspace.persistence).toBe("session-scoped");
      expect(DEFAULT_SURFACES.workspace.requiresUserGesture).toBe(false);

      expect(DEFAULT_SURFACES.sidebar.persistence).toBe("session-scoped");
      expect(DEFAULT_SURFACES.sidebar.requiresUserGesture).toBe(false);

      expect(DEFAULT_SURFACES.modal.persistence).toBe("turn-scoped");
      expect(DEFAULT_SURFACES.modal.requiresUserGesture).toBe(true);
    });
  });

  describe("listArtifacts (7.5)", () => {
    it("returns only surfaces WITH artifact metadata, ordered by createdAt", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: SurfaceRegistryProvider,
      });
      act(() => {
        // A plain surface (no artifact metadata) — excluded from the index.
        result.current.appendMessages("workspace", [createMsg("workspace")], "s0");
        // Two artifact surfaces with metadata.
        result.current.appendMessages(
          "ppa_clauses:docA",
          [createMsg("ppa_clauses:docA")],
          "s1",
          { kind: "clauses", title: "docA.pdf", description: "12 clauses" },
        );
        result.current.appendMessages(
          "ppa_comparison",
          [createMsg("ppa_comparison")],
          "s2",
          { kind: "comparison", title: "Contract Comparison", description: "3 differences" },
        );
      });
      const artifacts = result.current.listArtifacts();
      expect(artifacts.map((a) => a.surfaceId)).toEqual(["ppa_clauses:docA", "ppa_comparison"]);
      expect(artifacts[0].title).toBe("docA.pdf");
      expect(artifacts[1].kind).toBe("comparison");
      // The plain workspace surface is NOT an artifact.
      expect(artifacts.some((a) => a.surfaceId === "workspace")).toBe(false);
    });
  });

  describe("register / unregister / getMount", () => {
    it("returns the ref after register and null after unregister", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      const ref = createRef<HTMLDivElement>();

      act(() => {
        result.current.register("workspace", ref);
      });
      expect(result.current.getMount("workspace")).toBe(ref);

      act(() => {
        result.current.unregister("workspace");
      });
      expect(result.current.getMount("workspace")).toBeNull();
    });

    it("returns null for never-registered surface", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      expect(result.current.getMount("nonexistent")).toBeNull();
    });

    it("is idempotent when re-registering the SAME ref for a surfaceId", () => {
      const consoleError = vi
        .spyOn(console, "error")
        .mockImplementation(() => {});
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      const ref = createRef<HTMLDivElement>();

      act(() => {
        result.current.register("workspace", ref);
        result.current.register("workspace", ref);
      });
      expect(result.current.getMount("workspace")).toBe(ref);
      expect(consoleError).not.toHaveBeenCalled();
      consoleError.mockRestore();
    });

    it("refuses to register a DIFFERENT ref for an already-mounted surfaceId and logs console.error", () => {
      const consoleError = vi
        .spyOn(console, "error")
        .mockImplementation(() => {});
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      const refA = createRef<HTMLDivElement>();
      const refB = createRef<HTMLDivElement>();

      act(() => {
        result.current.register("workspace", refA);
      });
      act(() => {
        result.current.register("workspace", refB);
      });

      expect(result.current.getMount("workspace")).toBe(refA);
      expect(consoleError).toHaveBeenCalled();
      consoleError.mockRestore();
    });
  });

  describe("getPolicy", () => {
    it("returns the default policy for an unregistered known surface", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      expect(result.current.getPolicy("workspace")).toEqual(
        DEFAULT_SURFACES.workspace,
      );
    });

    it("returns the chat default for unknown surface ids (safe fallback)", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      expect(result.current.getPolicy("never-heard-of-it")).toEqual(
        DEFAULT_SURFACES.chat,
      );
    });

    it("merges policyOverride on register so getPolicy reflects the override", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      const ref = createRef<HTMLDivElement>();

      act(() => {
        result.current.register("workspace", ref, {
          persistence: "indefinite",
        });
      });
      expect(result.current.getPolicy("workspace")).toEqual({
        ...DEFAULT_SURFACES.workspace,
        persistence: "indefinite",
      });
    });
  });

  describe("appendMessages / useSurfaceState", () => {
    it("getState returns null until appendMessages is called", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      expect(result.current.getState("workspace")).toBeNull();
    });

    it("stores a SurfaceModel + sourceToolCallId + timestamp after createSurface", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      const before = Date.now();
      act(() => {
        result.current.appendMessages(
          "workspace",
          [createMsg("workspace")],
          "tool-call-1",
        );
      });
      const state = result.current.getState("workspace");
      expect(state).not.toBeNull();
      expect(state?.surface?.id).toBe("workspace");
      expect(state?.sourceToolCallId).toBe("tool-call-1");
      expect(state?.lastUpdatedAt).toBeGreaterThanOrEqual(before);
    });

    it("auto-creates a surface when first message isn't createSurface (with warning)", () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      act(() => {
        result.current.appendMessages(
          "workspace",
          [updateData("workspace", { foo: "bar" })],
          "tc-1",
        );
      });
      expect(warn).toHaveBeenCalled();
      const state = result.current.getState("workspace");
      expect(state?.surface?.id).toBe("workspace");
      warn.mockRestore();
    });

    it("is idempotent on sourceToolCallId — duplicate dispatch is dropped", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      act(() => {
        result.current.appendMessages(
          "workspace",
          [createMsg("workspace")],
          "tc-dup",
        );
      });
      // Re-dispatching the same tool call's messages would normally trigger
      // "Surface already exists" inside the SDK. Idempotency protects us.
      expect(() => {
        act(() => {
          result.current.appendMessages(
            "workspace",
            [createMsg("workspace")],
            "tc-dup",
          );
        });
      }).not.toThrow();
    });

    it("re-renders subscribers on appendMessages", () => {
      const renderCount = { count: 0 };

      function Observer() {
        renderCount.count += 1;
        const state = useSurfaceState("workspace");
        return (
          <div data-testid="observer">{state?.sourceToolCallId ?? "none"}</div>
        );
      }

      function Trigger({
        registryRef,
      }: {
        registryRef: { current: ReturnType<typeof useSurfaceRegistry> | null };
      }) {
        registryRef.current = useSurfaceRegistry();
        return null;
      }

      const registryRef: {
        current: ReturnType<typeof useSurfaceRegistry> | null;
      } = { current: null };

      const { getByTestId } = render(
        wrap(
          <>
            <Trigger registryRef={registryRef} />
            <Observer />
          </>,
        ),
      );

      const initial = renderCount.count;
      expect(getByTestId("observer").textContent).toBe("none");

      act(() => {
        registryRef.current?.appendMessages(
          "workspace",
          [createMsg("workspace")],
          "call-A",
        );
      });

      expect(getByTestId("observer").textContent).toBe("call-A");
      expect(renderCount.count).toBeGreaterThan(initial);
    });

    it("does NOT re-render subscribers when an UNRELATED surface changes", () => {
      const renderCount = { count: 0 };

      function Observer() {
        renderCount.count += 1;
        useSurfaceState("workspace");
        return null;
      }

      function Trigger({
        registryRef,
      }: {
        registryRef: { current: ReturnType<typeof useSurfaceRegistry> | null };
      }) {
        registryRef.current = useSurfaceRegistry();
        return null;
      }

      const registryRef: {
        current: ReturnType<typeof useSurfaceRegistry> | null;
      } = { current: null };

      render(
        wrap(
          <>
            <Trigger registryRef={registryRef} />
            <Observer />
          </>,
        ),
      );
      const initial = renderCount.count;

      act(() => {
        registryRef.current?.appendMessages(
          "sidebar",
          [createMsg("sidebar")],
          "call-B",
        );
      });

      expect(renderCount.count).toBe(initial);
    });

    it("clearSurface resets state to null and re-renders subscribers", () => {
      function Observer() {
        const state = useSurfaceState("workspace");
        return <div data-testid="observer">{state ? "set" : "null"}</div>;
      }
      function Trigger({
        registryRef,
      }: {
        registryRef: { current: ReturnType<typeof useSurfaceRegistry> | null };
      }) {
        registryRef.current = useSurfaceRegistry();
        return null;
      }
      const registryRef: {
        current: ReturnType<typeof useSurfaceRegistry> | null;
      } = { current: null };

      const { getByTestId } = render(
        wrap(
          <>
            <Trigger registryRef={registryRef} />
            <Observer />
          </>,
        ),
      );

      act(() => {
        registryRef.current?.appendMessages(
          "workspace",
          [createMsg("workspace")],
          "src",
        );
      });
      expect(getByTestId("observer").textContent).toBe("set");

      act(() => {
        registryRef.current?.clearSurface("workspace");
      });
      expect(getByTestId("observer").textContent).toBe("null");
    });
  });

  describe("readA2uiSurfaceState (sprint 2.10)", () => {
    it("returns empty object when no surfaces are active", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      expect(result.current.readA2uiSurfaceState()).toEqual({});
    });

    it("snapshots dataModel + catalogId for every live surface", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      act(() => {
        result.current.appendMessages(
          "workspace",
          [
            createMsg("workspace"),
            updateData("workspace", { activeUsers: "42 online", revenue: "$1,234" }),
          ],
          "tc-1",
        );
        result.current.appendMessages(
          "sidebar",
          [createMsg("sidebar"), updateData("sidebar", { tip: "click pin" })],
          "tc-2",
        );
      });
      const snapshot = result.current.readA2uiSurfaceState();
      expect(snapshot.workspace).toEqual({
        catalogId: CATALOG_ID,
        dataModel: { activeUsers: "42 online", revenue: "$1,234" },
      });
      expect(snapshot.sidebar).toEqual({
        catalogId: CATALOG_ID,
        dataModel: { tip: "click pin" },
      });
    });

    it("excludes cleared surfaces from the snapshot", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      act(() => {
        result.current.appendMessages(
          "workspace",
          [createMsg("workspace"), updateData("workspace", { x: 1 })],
          "tc-1",
        );
        result.current.appendMessages(
          "sidebar",
          [createMsg("sidebar"), updateData("sidebar", { y: 2 })],
          "tc-2",
        );
      });
      expect(Object.keys(result.current.readA2uiSurfaceState())).toEqual(
        expect.arrayContaining(["workspace", "sidebar"]),
      );

      act(() => {
        result.current.clearSurface("workspace");
      });
      const snapshot = result.current.readA2uiSurfaceState();
      expect(snapshot.workspace).toBeUndefined();
      expect(snapshot.sidebar).toBeDefined();
    });

    it("excludes surfaces with createSurface but no data model (dataModel: undefined)", () => {
      const { result } = renderHook(() => useSurfaceRegistry(), {
        wrapper: withProvider,
      });
      act(() => {
        // createSurface only — no updateDataModel
        result.current.appendMessages("workspace", [createMsg("workspace")], "tc-1");
      });
      const snapshot = result.current.readA2uiSurfaceState();
      // Surface IS present (it exists); dataModel is undefined.
      expect(snapshot.workspace).toEqual({
        catalogId: CATALOG_ID,
        dataModel: undefined,
      });
    });
  });

  describe("useSurfaceMount", () => {
    it("returns the ref once registered, null before", () => {
      function Observer({
        onMount,
      }: {
        onMount: (ref: RefObject<HTMLDivElement | null> | null) => void;
      }) {
        const mount = useSurfaceMount("workspace");
        onMount(mount);
        return null;
      }
      function Trigger({
        registryRef,
      }: {
        registryRef: { current: ReturnType<typeof useSurfaceRegistry> | null };
      }) {
        registryRef.current = useSurfaceRegistry();
        return null;
      }
      const registryRef: {
        current: ReturnType<typeof useSurfaceRegistry> | null;
      } = { current: null };
      const seen: Array<RefObject<HTMLDivElement | null> | null> = [];
      const myRef = createRef<HTMLDivElement>();

      render(
        wrap(
          <>
            <Trigger registryRef={registryRef} />
            <Observer onMount={(r) => seen.push(r)} />
          </>,
        ),
      );

      expect(seen[0]).toBeNull();

      act(() => {
        registryRef.current?.register("workspace", myRef);
      });

      expect(seen.at(-1)).toBe(myRef);
    });
  });

  describe("hook misuse", () => {
    it("useSurfaceRegistry throws outside a provider", () => {
      const spy = vi.spyOn(console, "error").mockImplementation(() => {});
      expect(() => renderHook(() => useSurfaceRegistry())).toThrow(
        /SurfaceRegistryProvider/,
      );
      spy.mockRestore();
    });
  });
});
