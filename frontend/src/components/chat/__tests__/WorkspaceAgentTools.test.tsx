import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceAgentTools } from "../WorkspaceAgentTools";
import type { WebMCPTool } from "@/lib/webmcp/workspaceTools";

const fixture = vi.hoisted(() => ({ user: { uid: "user" } as { uid: string } | null, registry: { listArtifacts: vi.fn(() => []), getState: vi.fn(), getMount: vi.fn() } }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: fixture.user, loading: false }) }));
vi.mock("@/providers/SurfaceRegistry", () => ({ useSurfaceRegistry: () => fixture.registry }));

describe("workspace WebMCP gate", () => {
  const tools = new Map<string, WebMCPTool>();
  const api = { registerTool: vi.fn((tool: WebMCPTool, { signal }: { signal: AbortSignal }) => { tools.set(tool.name, tool); signal.addEventListener("abort", () => tools.delete(tool.name)); }) };
  beforeEach(() => {
    fixture.user = { uid: "user" }; tools.clear(); vi.clearAllMocks();
    vi.stubEnv("NEXT_PUBLIC_WEBMCP_ENABLED", "true");
    Object.defineProperty(document, "modelContext", { configurable: true, value: api });
  });
  afterEach(() => { vi.unstubAllEnvs(); Reflect.deleteProperty(document, "modelContext"); });
  const props = { sessionId: "session", skillId: "skill", running: false, onFocus: vi.fn() };
  it("is off by default and never registers in unsupported browsers", () => {
    vi.stubEnv("NEXT_PUBLIC_WEBMCP_ENABLED", "false");
    const { rerender } = render(<WorkspaceAgentTools {...props} />);
    expect(api.registerTool).not.toHaveBeenCalled();
    vi.stubEnv("NEXT_PUBLIC_WEBMCP_ENABLED", "true");
    Reflect.deleteProperty(document, "modelContext");
    rerender(<WorkspaceAgentTools {...props} />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
  it("requires explicit enablement, unregisters on stop and on logout", async () => {
    const { rerender } = render(<WorkspaceAgentTools {...props} />);
    expect(tools.size).toBe(0);
    fireEvent.click(screen.getByText("Enable agent diagnostics"));
    expect(tools.size).toBe(3);
    fireEvent.click(screen.getByText("Stop agent diagnostics"));
    expect(tools.size).toBe(0);
    fireEvent.click(screen.getByText("Enable agent diagnostics"));
    const retained = tools.get("get_workspace_state")!;
    fixture.user = null;
    rerender(<WorkspaceAgentTools {...props} />);
    expect(tools.size).toBe(0);
    await act(async () => { expect(JSON.parse((await retained.execute({})).content[0].text).error).toBe("diagnostics_disabled"); });
  });
  it("requires fresh enablement after session change and cleans up on unmount", () => {
    const { rerender, unmount } = render(<WorkspaceAgentTools {...props} />);
    fireEvent.click(screen.getByText("Enable agent diagnostics"));
    rerender(<WorkspaceAgentTools {...props} sessionId="new" />);
    expect(tools.size).toBe(0);
    rerender(<WorkspaceAgentTools {...props} />);
    expect(tools.size).toBe(0);
    fireEvent.click(screen.getByText("Enable agent diagnostics"));
    unmount(); expect(tools.size).toBe(0);
  });
});
