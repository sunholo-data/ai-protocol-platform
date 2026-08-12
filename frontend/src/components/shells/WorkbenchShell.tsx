"use client";

import { useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import type { User } from "@/lib/firebase";
import type { SkillShell } from "@/types/skill";
import { useSkillAgent } from "@/hooks/useSkillAgent";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { Workbench, type WorkbenchTab } from "@/components/chat/Workbench";
import {
  SurfaceRegistryProvider,
  useClearSurfacesOnSessionChange,
} from "@/providers/SurfaceRegistry";
import { ChatDrawer } from "@/components/shells/ChatDrawer";
import { DrawerChatPane } from "@/components/shells/DrawerChatPane";

export interface WorkbenchShellProps {
  skillId: string;
  pathPrefix: string;
  user: User;
  shell: SkillShell | null;
}

/**
 * v6.4.0 SHELL-MODES — workbench-primary page shell.
 *
 * The Workbench fills the viewport with skill-declared tabs (or a single
 * workspace-surface tab when none are declared), and chat is a left-edge
 * drawer. The "agent drives the UI" pattern moves up from the surface level
 * (which A2UI region) to the page level (which shell + which tabs).
 *
 * No production skill targets `workbench-primary` in this sprint — this is the
 * extensibility path. The a2ui content source is fully wired; mcp_app and
 * fixed render an explicit "not yet wired" placeholder rather than silently
 * showing nothing (full mcp_app iframe wiring is a follow-up).
 */
export function WorkbenchShell({ skillId, pathPrefix, user, shell }: WorkbenchShellProps) {
  const drawerState = shell?.chat?.defaultState === "open" ? "open" : "minimised";
  return (
    <SurfaceRegistryProvider>
      <WorkbenchShellInner
        skillId={skillId}
        pathPrefix={pathPrefix}
        user={user}
        shell={shell}
        drawerState={drawerState}
      />
    </SurfaceRegistryProvider>
  );
}

/** Resolve a tab's `content_source` directive to a React node.
 * - `a2ui:<surfaceId>`  → the named A2UI surface (fully wired)
 * - `mcp_app:<server>`  → MCP App iframe (placeholder in v1)
 * - `fixed:<component>` → shell-resolved component (v6.5 extensibility hook) */
function resolveTabContent(contentSource: string, sessionId: string | null): ReactNode {
  if (contentSource.startsWith("a2ui:")) {
    const surfaceId = contentSource.slice("a2ui:".length);
    return <A2UISurfaceMount surfaceId={surfaceId} className="h-full" sessionId={sessionId} />;
  }
  const [kind, detail] = contentSource.includes(":")
    ? [contentSource.slice(0, contentSource.indexOf(":")), contentSource.slice(contentSource.indexOf(":") + 1)]
    : [contentSource, ""];
  return (
    <div
      data-testid="workbench-tab-unsupported"
      data-kind={kind}
      className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground"
    >
      {kind === "mcp_app"
        ? `MCP App "${detail}" — iframe wiring is a follow-up.`
        : kind === "fixed"
          ? `Fixed component "${detail}" — reserved for v6.5.`
          : `Unrecognised content source "${contentSource}".`}
    </div>
  );
}

function WorkbenchShellInner({
  skillId,
  pathPrefix,
  user,
  shell,
  drawerState,
}: {
  skillId: string;
  pathPrefix: string;
  user: User;
  shell: SkillShell | null;
  drawerState: "open" | "minimised";
}) {
  const { sessionId: agentSessionId } = useSkillAgent();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session") ?? agentSessionId;
  useClearSurfacesOnSessionChange(sessionId);

  const declaredTabs = shell?.workbench?.tabs ?? [];
  const tabs: WorkbenchTab[] =
    declaredTabs.length > 0
      ? declaredTabs.map((t) => ({
          id: t.id,
          label: t.label,
          content: resolveTabContent(t.contentSource, sessionId),
        }))
      : [
          {
            // Fallback: no declared tabs → mount the workspace surface, parity
            // with the ChatShell workbench's dynamic A2UI behaviour.
            id: "workspace",
            label: "Workspace",
            content: <A2UISurfaceMount surfaceId="workspace" className="h-full" sessionId={sessionId} />,
            emptyBody: "The assistant's structured outputs appear here as it works.",
          },
        ];

  const initialTab = shell?.workbench?.defaultTab ?? tabs[0]?.id ?? "workspace";
  const [activeTabId, setActiveTabId] = useState(initialTab);

  return (
    <div className="flex min-h-0 flex-1 w-full overflow-hidden">
      <ChatDrawer side="left" defaultState={drawerState} label="Chat" width={400}>
        <DrawerChatPane skillId={skillId} pathPrefix={pathPrefix} user={user} />
      </ChatDrawer>
      <div className="min-w-0 flex-1 overflow-hidden">
        <Workbench tabs={tabs} activeTabId={activeTabId} onActiveTabChange={setActiveTabId} />
      </div>
    </div>
  );
}
