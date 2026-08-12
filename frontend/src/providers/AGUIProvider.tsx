// Workshop W5c — AG-UI: The Frontend Provider
// We use @ag-ui/client directly instead of wrapping in <CopilotKit>. CopilotKit's
// `runtimeUrl` expects a GraphQL CopilotKit-Runtime endpoint, not a bare AG-UI SSE
// stream. Going AG-UI-native keeps the stack one layer thinner and avoids silent
// failures where 200 OK masked every message being dropped at the GraphQL layer.
// See memory: gotcha_copilotkit_not_agui_native.md for the full incident.

"use client";

import { HttpAgent } from "@ag-ui/client";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
} from "react";
import { subscribeToIdToken } from "@/lib/firebase";

/**
 * AG-UI-native provider. Exposes one `HttpAgent` per `skillId`, targeting the
 * backend's SSE endpoint via the Next `/api/proxy` forwarder. The
 * agent's `Authorization: Bearer …` header is mutated in place when
 * Firebase rotates the ID token (silently, ~hourly), so long-running
 * sessions never hit a 401 wall mid-conversation.
 *
 * We intentionally skip CopilotKit's `<CopilotKit>` provider here: its
 * `runtimeUrl` is a CopilotKit-Runtime GraphQL endpoint, not a bare AG-UI
 * SSE one. Going AG-UI-native with `@ag-ui/client` keeps the protocol stack
 * one layer thinner and matches the design doc's "thin client, fat protocol"
 * framing. If we later adopt `<CopilotChat>` for off-the-shelf UI polish,
 * we'll wrap it here alongside — not in place of — this context.
 */
const AGUIAgentContext = createContext<HttpAgent | null>(null);

export function AGUIProvider({
  skillId,
  sessionId,
  children,
}: {
  skillId: string;
  /** Resume an existing chat by seeding the HttpAgent's threadId. When
   * absent, the agent generates a fresh UUID — that becomes the new
   * session id, which the page should then write to the URL. */
  sessionId?: string;
  children: ReactNode;
}) {
  // G38 (template-chat-surface-defaults.md): DO NOT add a render gate
  // like `if (!tokenResolved) return null` here. Children must stay
  // mounted across silent Firebase ID-token refreshes (~hourly +
  // every onAuthStateChanged fire). G40 (template-auth-token-refresh.md)
  // mutates the HttpAgent's Authorization header IN PLACE when a new
  // token lands — no agent rebuild, no request goes out unauthenticated,
  // no extra GET /messages fires because consumers like
  // useSessionMessages keep their local state.
  //
  // The AIPLA fork added an unmount gate "defensively" and burned a
  // sprint debugging mid-chat flicker on cross-region deploys (Cloud
  // Run europe-north1 → Vertex Agent Engine europe-west1, ~400ms blank
  // window per refresh). Fixed by `hadTokenOnceRef` + first-load-only
  // gating in commit cphu-aipla-app@9eac9eb. Upstream Aitana never
  // had the gate; never had the bug. Don't introduce it.
  //
  // Why useMemo dep list excludes the token (G40 — template-auth-token-refresh.md):
  // the agent instance is stable across token rotations. We mutate
  // ``agent.headers.Authorization`` in place from the subscribeToIdToken
  // effect below. Rebuilding the agent on every silent ~hourly refresh
  // is wasteful AND throws away any in-flight SSE stream state.
  const agent = useMemo(() => {
    return new HttpAgent({
      url: `/api/proxy/api/skill/${encodeURIComponent(skillId)}/stream`,
      headers: {},
      threadId: sessionId,
    });
  }, [skillId, sessionId]);

  // G40 (template-auth-token-refresh.md): subscribe to token rotations
  // and mutate the agent's Authorization header in place. Without this,
  // the token fetched at mount expires ~1h later and every subsequent
  // runAgent() call returns 401 — the agent appears "broken" mid-session
  // and only a page refresh fixes it. The subscriber fires immediately
  // with the current token (so the first runAgent() is authenticated)
  // AND on every Firebase ``onIdTokenChanged`` event thereafter.
  useEffect(() => {
    const unsubscribe = subscribeToIdToken((token) => {
      // Mutate the headers object the HttpAgent reads on every request.
      // `Record<string, string>` cast preserves the existing object
      // identity (don't replace `agent.headers` — some HttpAgent
      // implementations may hold a stale reference to the original).
      const headers = agent.headers as Record<string, string>;
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      } else {
        delete headers.Authorization;
      }
    });
    return unsubscribe;
  }, [agent]);

  return (
    <AGUIAgentContext.Provider value={agent}>
      {children}
    </AGUIAgentContext.Provider>
  );
}

export function useAGUIAgent(): HttpAgent {
  const agent = useContext(AGUIAgentContext);
  if (!agent) {
    throw new Error("useAGUIAgent must be used within an AGUIProvider");
  }
  return agent;
}
