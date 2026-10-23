import { describe, expect, it, vi } from "vitest";
import { registerWorkspaceTools, type WorkspaceSnapshot, type WebMCPTool } from "../workspaceTools";

function setup() {
  const tools = new Map<string, WebMCPTool>();
  const api = { registerTool: vi.fn((tool: WebMCPTool, { signal }: { signal: AbortSignal }) => { tools.set(tool.name, tool); signal.addEventListener("abort", () => tools.delete(tool.name)); }) };
  let snapshot: WorkspaceSnapshot | null = { identity: "private-user/private-session", running: false, surfaces: [{ id: "private-title", registered: true, mounted: false, visible: false, revision: 10 }] };
  const focus = vi.fn();
  const stop = registerWorkspaceTools(api, () => snapshot, focus);
  const call = async (name: string, args = {}) => JSON.parse((await tools.get(name)!.execute(args)).content[0].text);
  return { tools, api, stop, call, focus, setSnapshot: (next: WorkspaceSnapshot | null) => { snapshot = next; } };
}

describe("workspace diagnostics", () => {
  it("returns only allowlisted facts and distinguishes registration from visibility", async () => {
    const f = setup();
    const state = await f.call("get_workspace_state");
    expect(JSON.stringify(state)).not.toContain("private");
    expect(state.surfaces[0]).toMatchObject({ registered: true, mounted: false, visible: false, visualInspection: "not_performed" });
    expect(await f.call("inspect_surface", { contextRef: state.contextRef, surfaceRef: state.surfaces[0].surfaceRef })).toMatchObject({ mounted: false });
  });
  it("rejects unknown references, stale sessions, and logout", async () => {
    const f = setup();
    const old = await f.call("get_workspace_state");
    expect(await f.call("focus_asset", { contextRef: old.contextRef, surfaceRef: "missing" })).toEqual({ error: "surface_not_found" });
    f.setSnapshot({ identity: "new-session", running: false, surfaces: [] });
    expect(await f.call("inspect_surface", { contextRef: old.contextRef, surfaceRef: old.surfaces[0].surfaceRef })).toEqual({ error: "stale_context" });
    f.setSnapshot(null);
    expect(await f.call("get_workspace_state")).toEqual({ error: "diagnostics_disabled" });
  });
  it("uses the existing focus handler without claiming render completion", async () => {
    const f = setup(); const state = await f.call("get_workspace_state");
    expect(await f.call("focus_asset", { contextRef: state.contextRef, surfaceRef: state.surfaces[0].surfaceRef })).toEqual({ status: "focus_requested" });
    expect(f.focus).toHaveBeenCalledWith("private-title");
  });
  it("refuses focus while a run is active", async () => {
    const f = setup();
    f.setSnapshot({ identity: "session", running: true, surfaces: [{ id: "asset", registered: true, mounted: false, visible: false, revision: 1 }] });
    const state = await f.call("get_workspace_state");
    expect(await f.call("focus_asset", { contextRef: state.contextRef, surfaceRef: state.surfaces[0].surfaceRef })).toEqual({ error: "run_in_progress" });
    expect(f.focus).not.toHaveBeenCalled();
  });
  it("revokes retained callbacks and unregisters only its own tools on stop", async () => {
    const f = setup(); const retained = f.tools.get("get_workspace_state")!;
    f.stop();
    expect(f.tools.size).toBe(0);
    expect(JSON.parse((await retained.execute({})).content[0].text)).toEqual({ error: "diagnostics_disabled" });
  });
  it("revokes partial registration on asynchronous failure", async () => {
    const retained: WebMCPTool[] = [];
    const signals: AbortSignal[] = [];
    const onError = vi.fn();
    registerWorkspaceTools({ registerTool: async (tool, { signal }) => {
      retained.push(tool); signals.push(signal);
      if (retained.length === 2) throw new Error("duplicate");
    } }, () => ({ identity: "session", running: false, surfaces: [] }), vi.fn(), onError);
    await Promise.resolve(); await Promise.resolve();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(JSON.parse((await retained[0].execute({})).content[0].text).error).toBe("diagnostics_disabled");
  });
  it("aborts pending registration when stopped before resolution", async () => {
    let signal: AbortSignal | undefined;
    const onError = vi.fn();
    const stop = registerWorkspaceTools({ registerTool: (_tool, options) => {
      signal = options.signal;
      return Promise.reject(new Error("aborted"));
    } }, () => null, vi.fn(), onError);
    stop(); await Promise.resolve();
    expect(signal?.aborted).toBe(true);
    expect(onError).not.toHaveBeenCalled();
  });
});
