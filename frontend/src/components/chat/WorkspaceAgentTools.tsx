"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useSurfaceRegistry } from "@/providers/SurfaceRegistry";
import { getModelContext, registerWorkspaceTools } from "@/lib/webmcp/workspaceTools";

type Props = {
  sessionId: string | null;
  skillId: string;
  running: boolean;
  onFocus: (surfaceId: string) => void;
};

/** Default off, explicit per-session enablement; no customer text crosses this boundary. */
export function WorkspaceAgentTools(props: Props) {
  if (process.env.NEXT_PUBLIC_WEBMCP_ENABLED !== "true") return null;
  return <EnabledWorkspaceAgentTools {...props} />;
}

function EnabledWorkspaceAgentTools(props: Props) {
  const { user, loading } = useAuth();
  const registry = useSurfaceRegistry();
  const identity = JSON.stringify([user?.uid, props.skillId, props.sessionId]);
  const [allowedIdentity, setAllowedIdentity] = useState<string | null>(null);
  const [supported, setSupported] = useState(false);
  const [failed, setFailed] = useState(false);
  const enabled = !!user && !loading && allowedIdentity === identity;
  const latest = useRef({ enabled, identity, registry, ...props });
  // Commit current state before browser callbacks can run after navigation/logout.
  useLayoutEffect(() => { latest.current = { enabled, identity, registry, ...props }; });
  useEffect(() => {
    if (!user || loading || allowedIdentity !== identity) setAllowedIdentity(null);
  }, [identity, user, loading, allowedIdentity]);
  useEffect(() => { setSupported(!!getModelContext(document)); }, []);
  useEffect(() => {
    if (!enabled) return;
    const api = getModelContext(document);
    if (!api) return;
    try {
      return registerWorkspaceTools(api, () => {
        const current = latest.current;
        if (!current.enabled || current.identity !== identity) return null;
        return {
          identity: current.identity,
          running: current.running,
          surfaces: [
            ...current.registry.listArtifacts().filter((item) => item.placement !== "chat"),
            ...(current.registry.getState("workspace")?.surface ? [{ surfaceId: "workspace" }] : []),
          ].map((item) => {
            const state = current.registry.getState(item.surfaceId);
            const mount = current.registry.getMount(item.surfaceId);
            const element = mount?.current;
            // Specialised workbench views do not all register DOM refs. Unknown
            // is distinct from absent; never infer DOM readiness from state.
            const mounted = mount ? !!element?.isConnected : null;
            const visible = mounted === null ? null : !!element?.isConnected && element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible";
            return { id: item.surfaceId, registered: !!state?.surface, mounted, visible, revision: state?.lastUpdatedAt ?? 0 };
          }),
        };
      }, (id) => latest.current.onFocus(id), () => {
        setFailed(true);
        setAllowedIdentity(null);
      });
    } catch {
      setFailed(true);
      setAllowedIdentity(null);
    }
  }, [enabled, identity]);

  if (!user || loading) return null;
  return <div className="flex items-center gap-2 px-3 py-1 text-xs text-muted-foreground">
    <button type="button" className="underline" disabled={!supported} onClick={() => { setFailed(false); setAllowedIdentity(enabled ? null : identity); }}>
      {enabled ? "Stop agent diagnostics" : "Enable agent diagnostics"}
    </button>
    <span role="status">{failed ? "Agent diagnostics could not start." : !supported ? "WebMCP is unavailable in this browser." : enabled ? "Agent diagnostics active: lifecycle metadata only." : "Off for this session."}</span>
  </div>;
}
