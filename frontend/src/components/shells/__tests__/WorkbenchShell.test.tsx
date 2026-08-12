import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { SkillShell } from "@/types/skill";

vi.mock("@/hooks/useSkillAgent", () => ({
  useSkillAgent: () => ({ sessionId: "agent-session-1" }),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("@/providers/SurfaceRegistry", () => ({
  SurfaceRegistryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useClearSurfacesOnSessionChange: () => undefined,
}));

vi.mock("@/components/protocols/A2UISurfaceMount", () => ({
  A2UISurfaceMount: ({ surfaceId }: { surfaceId: string }) => (
    <div data-testid="a2ui-surface-mount" data-surface={surfaceId} />
  ),
}));

// Render every tab's content so content_source resolution is assertable.
vi.mock("@/components/chat/Workbench", () => ({
  Workbench: ({ tabs }: { tabs: Array<{ id: string; content: React.ReactNode }> }) => (
    <div data-testid="workbench">
      {tabs.map((t) => (
        <div key={t.id} data-tab={t.id}>
          {t.content}
        </div>
      ))}
    </div>
  ),
}));

vi.mock("@/components/shells/DrawerChatPane", () => ({
  DrawerChatPane: () => <div data-testid="drawer-chat-pane" />,
}));

import { WorkbenchShell } from "@/components/shells/WorkbenchShell";

const baseProps = {
  skillId: "skill-1",
  pathPrefix: "/chat/@owner/wb",
  user: { uid: "u1", displayName: "Mark", email: "mark@x.com", photoURL: null } as never,
};

function renderShell(shell: SkillShell | null) {
  return render(<WorkbenchShell {...baseProps} shell={shell} />);
}

describe("WorkbenchShell", () => {
  it("resolves an a2ui content_source to the named A2UI surface", () => {
    renderShell({
      mode: "workbench-primary",
      workbench: { tabs: [{ id: "t1", label: "Compare", contentSource: "a2ui:workspace" }] },
    });
    const mount = screen.getByTestId("a2ui-surface-mount");
    expect(mount.getAttribute("data-surface")).toBe("workspace");
  });

  it("renders an explicit placeholder for an mcp_app content_source (v1)", () => {
    renderShell({
      mode: "workbench-primary",
      workbench: { tabs: [{ id: "src", label: "Sources", contentSource: "mcp_app:gcs-browser" }] },
    });
    const ph = screen.getByTestId("workbench-tab-unsupported");
    expect(ph.getAttribute("data-kind")).toBe("mcp_app");
  });

  it("renders an explicit placeholder for a fixed content_source (v6.5 hook)", () => {
    renderShell({
      mode: "workbench-primary",
      workbench: { tabs: [{ id: "f", label: "Fixed", contentSource: "fixed:SomeComponent" }] },
    });
    const ph = screen.getByTestId("workbench-tab-unsupported");
    expect(ph.getAttribute("data-kind")).toBe("fixed");
  });

  it("falls back to a workspace-surface tab when no tabs are declared", () => {
    renderShell({ mode: "workbench-primary" });
    const mount = screen.getByTestId("a2ui-surface-mount");
    expect(mount.getAttribute("data-surface")).toBe("workspace");
  });

  it("docks chat in a left-side drawer", () => {
    renderShell({ mode: "workbench-primary" });
    const drawer = screen.getByTestId("chat-drawer");
    expect(drawer.getAttribute("data-side")).toBe("left");
    expect(screen.getByTestId("drawer-chat-pane")).not.toBeNull();
  });
});
