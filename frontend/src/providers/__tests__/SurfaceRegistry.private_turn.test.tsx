import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { useSkillAgent } from "@/hooks/useSkillAgent";
import { SurfaceRegistryProvider, useClearSurfacesOnSessionChange, useSurfaceRegistry } from "../SurfaceRegistry";

const fake = vi.hoisted(() => ({
  threadId: "session-a", messages: [],
  subscribe: vi.fn(() => ({ unsubscribe: vi.fn() })),
  runAgent: vi.fn(async (_input?: unknown) => ({ newMessages: [] })),
  abortRun: vi.fn(), addMessage: vi.fn(),
}));
vi.mock("@/providers/AGUIProvider", () => ({ useAGUIAgent: () => fake }));

// Real A2UI processor, registry, and ordinary chat-send hook; only the model
// transport is fake. This also exercises registration with the shipped SDK.
it("a subsequent ordinary chat turn never exports private details", async () => {
  const { result, rerender } = renderHook(({ sessionId }) => {
    useClearSurfacesOnSessionChange(sessionId);
    return { registry: useSurfaceRegistry(), agent: useSkillAgent() };
  }, {
    initialProps: { sessionId: "session-a" },
    wrapper: ({ children }: { children: ReactNode }) => <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>,
  });
  const id = "private:board:review";
  const catalogId = "https://a2ui.org/specification/v0_9/basic_catalog.json";
  const create = (surfaceId: string) => ({ version: "v0.9", createSurface: { surfaceId, catalogId } });
  act(() => {
    result.current.registry.appendMessages("board", [create("board"), {
      version: "v0.9", updateDataModel: { surfaceId: "board", value: { count: 1 } },
    }], "board");
    result.current.registry.appendPrivateMessages(id, [create(id), {
      version: "v0.9", updateDataModel: { surfaceId: id, value: { purpose: "private-purpose-canary", arguments: "private-argument-canary" } },
    }], "review", { kind: "review", title: "Private review" });
  });
  expect(result.current.registry.getState(id)?.surface?.dataModel.get("/purpose")).toBe("private-purpose-canary");
  await act(async () => { await result.current.agent.sendMessage("What else is waiting?"); });
  expect(fake.runAgent).toHaveBeenCalledWith({ forwardedProps: { a2ui_surface_state: {
    board: { catalogId, dataModel: { count: 1 } },
  } } });
  expect(JSON.stringify(fake.runAgent.mock.calls)).not.toContain("private-purpose-canary");
  expect(JSON.stringify(fake.runAgent.mock.calls)).not.toContain("private-argument-canary");
  rerender({ sessionId: "session-b" });
  expect(result.current.registry.getState(id)).toBeNull();
});


it("a private delete disposes detail without creating an empty artifact", () => {
  const { result } = renderHook(() => useSurfaceRegistry(), {
    wrapper: ({ children }: { children: ReactNode }) => <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>,
  });
  const id = "private:board:review";
  const remove = { version: "v0.9", deleteSurface: { surfaceId: id } };
  act(() => { result.current.appendPrivateMessages(id, [remove], "initial-clear"); });
  expect(result.current.listArtifacts()).toEqual([]);
  act(() => { result.current.appendPrivateMessages(id, [{ version: "v0.9", createSurface: { surfaceId: id, catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json" } }], "review", { kind: "review", title: "Private review" }); });
  expect(result.current.listArtifacts()).toHaveLength(1);
  act(() => { result.current.appendPrivateMessages(id, [remove], "decision-clear"); });
  expect(result.current.listArtifacts()).toEqual([]);
  expect(result.current.readA2uiSurfaceState()).toEqual({});
});
