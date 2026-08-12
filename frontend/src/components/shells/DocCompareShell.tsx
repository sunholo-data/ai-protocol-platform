"use client";

import { useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { User } from "@/lib/firebase";
import type { ExampleDocument, SkillShell } from "@/types/skill";
import { useSkillAgent } from "@/hooks/useSkillAgent";
import { useSkillMeta } from "@/hooks/useSkillMeta";
import { importByReference, isImportError } from "@/lib/importByReference";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import {
  SurfaceRegistryProvider,
  useClearSurfacesOnSessionChange,
  useSurfaceState,
} from "@/providers/SurfaceRegistry";
import { ChatDrawer } from "@/components/shells/ChatDrawer";
import { DrawerChatPane } from "@/components/shells/DrawerChatPane";
import { SkillExamplesPicker } from "@/components/chat/SkillExamplesPicker";
import { GCSFileBrowser } from "@/components/doc-browser/GCSFileBrowser";

export interface DocCompareShellProps {
  skillId: string;
  pathPrefix: string;
  user: User;
  shell: SkillShell | null;
}

/**
 * v6.4.0 SHELL-MODES — doc-compare page shell.
 *
 * Since 7.3/7.5 (tool-results-as-a2ui) the comparison renders as A2UI artifact
 * tabs via the server-side result→A2UI mapping and the generic
 * `A2UISurfaceMount` — the bespoke `SideBySideDocViewer` / `KeyDifferencesPanel`
 * panels were retired. This shell makes the workbench fill the viewport and
 * demotes chat to a right-edge drawer, instead of the chat-primary column +
 * sidebar of ChatShell. (Production standardised on ChatShell for
 * one-doc-compare; this shell is retained for the doc-compare shell mode.)
 *
 * Auth is gated upstream in the chat route (SignInRequired) before this shell
 * ever renders, so `user` is always present here.
 */
export function DocCompareShell({ skillId, pathPrefix, user, shell }: DocCompareShellProps) {
  const drawerState = shell?.chat?.defaultState === "open" ? "open" : "minimised";
  return (
    <SurfaceRegistryProvider>
      <DocCompareShellInner
        skillId={skillId}
        pathPrefix={pathPrefix}
        user={user}
        drawerState={drawerState}
      />
    </SurfaceRegistryProvider>
  );
}

interface SelectedDoc {
  id: string;
  label: string;
}

function DocCompareShellInner({
  skillId,
  pathPrefix,
  user,
  drawerState,
}: {
  skillId: string;
  pathPrefix: string;
  user: User;
  drawerState: "open" | "minimised";
}) {
  const { sessionId: agentSessionId, sendMessage, isLoading } = useSkillAgent();
  const searchParams = useSearchParams();
  const urlSessionId = searchParams.get("session");
  const sessionId = urlSessionId ?? agentSessionId;
  // Clear session-scoped surfaces (the comparison) when the user switches
  // sessions — same lifecycle policy ChatShell applies.
  useClearSurfacesOnSessionChange(sessionId);

  const [selected, setSelected] = useState<SelectedDoc[]>([]);

  // Pick → parse via import-by-reference (the doc lands as a session ARTIFACT,
  // not inlined), then track its id for selection. We never dump document text
  // into context here — only the doc id, which the loader turns into an
  // on-demand artifact the compare tool reads selectively.
  const onPickImport = useCallback(
    async (bucket: string, object: string, label: string) => {
      const result = await importByReference(bucket, object, skillId);
      if (isImportError(result)) {
        console.error(`doc-compare import-by-reference failed: ${result.message}`);
        return;
      }
      const id = result.doc.id;
      setSelected((prev) => (prev.some((d) => d.id === id) ? prev : [...prev, { id, label }]));
    },
    [skillId],
  );

  const removeSelected = useCallback((id: string) => {
    setSelected((prev) => prev.filter((d) => d.id !== id));
  }, []);

  // "Compare these two" → one agent turn carrying ONLY the two document ids via
  // forwardedProps.document_ids (artifact path). resumedSession is left unset,
  // so the eager full-doc inline is NOT triggered — the agent's compare tool
  // pulls the clauses it needs from the artifacts. Safe for 60–137pp PPAs.
  const onCompare = useCallback(() => {
    if (selected.length !== 2 || isLoading) return;
    void sendMessage("Compare these two PPA contracts and summarise the key commercial differences.", {
      documentIds: selected.map((d) => d.id),
    });
  }, [selected, isLoading, sendMessage]);

  return (
    <div className="flex min-h-0 flex-1 w-full overflow-hidden">
      <WorkspaceCanvas
        sessionId={sessionId}
        skillId={skillId}
        selected={selected}
        comparing={isLoading}
        onPickImport={onPickImport}
        onRemove={removeSelected}
        onCompare={onCompare}
      />
      <ChatDrawer side="right" defaultState={drawerState} label="Chat" width={400}>
        <DrawerChatPane
          skillId={skillId}
          pathPrefix={pathPrefix}
          user={user}
          placeholder="Ask about the comparison…"
        />
      </ChatDrawer>
    </div>
  );
}

/** The viewport-filling comparison surface. Once the agent renders a comparison
 * into the A2UI workspace surface, that takes over. Before then, the empty state
 * offers the skill's example PPAs + bucket library so the user can load the two
 * contracts (v6.5.0 BUCKET-FILES — DocCompareShell has no sidebar, so the file
 * affordances ChatShell puts in the sidebar live here instead). Picked docs are
 * collected; selecting two reveals a "Compare these two" button that runs the
 * comparison via the artifact path. */
function WorkspaceCanvas({
  sessionId,
  skillId,
  selected,
  comparing,
  onPickImport,
  onRemove,
  onCompare,
}: {
  sessionId: string | null;
  skillId: string;
  selected: SelectedDoc[];
  comparing: boolean;
  onPickImport: (bucket: string, object: string, label: string) => void;
  onRemove: (id: string) => void;
  onCompare: () => void;
}) {
  const state = useSurfaceState("workspace");
  const { welcome, initialMessage } = useSkillMeta(skillId);

  if (state?.surface) {
    return (
      <div data-testid="doc-compare-canvas" className="min-w-0 flex-1 overflow-auto p-3">
        <A2UISurfaceMount surfaceId="workspace" className="h-full" sessionId={sessionId} />
      </div>
    );
  }

  const examples: ExampleDocument[] = welcome?.exampleDocuments ?? [];
  const bucketBrowser = welcome?.bucketBrowser ?? null;
  const hasFileAffordances = examples.length > 0 || bucketBrowser != null;
  const prompt =
    (welcome?.introMessage && welcome.introMessage.trim()) ||
    (initialMessage && initialMessage.trim()) ||
    "Pick two contracts and I'll show you the differences side-by-side.";

  if (!hasFileAffordances) {
    return (
      <div
        data-testid="doc-compare-empty"
        className="flex min-w-0 flex-1 items-center justify-center p-8 text-center"
      >
        <p className="max-w-md text-sm text-muted-foreground">{prompt}</p>
      </div>
    );
  }

  return (
    <div data-testid="doc-compare-files" className="min-w-0 flex-1 overflow-auto p-4">
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">{prompt}</p>

      {/* Selected docs + the compare affordance */}
      {selected.length > 0 && (
        <div data-testid="doc-compare-selected" className="mb-4 rounded-lg border bg-muted/30 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Selected ({selected.length})
          </p>
          <ul className="mb-3 flex flex-wrap gap-2">
            {selected.map((d) => (
              <li key={d.id} className="flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-xs">
                <span className="max-w-[16rem] truncate">{d.label}</span>
                <button
                  type="button"
                  aria-label={`Remove ${d.label}`}
                  onClick={() => onRemove(d.id)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          {selected.length === 2 ? (
            <button
              type="button"
              data-testid="doc-compare-run"
              onClick={onCompare}
              disabled={comparing}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {comparing ? "Comparing…" : "Compare these two →"}
            </button>
          ) : (
            <p className="text-xs text-muted-foreground">
              {selected.length < 2 ? "Pick one more to compare." : "Remove one — comparison takes exactly two."}
            </p>
          )}
        </div>
      )}

      {examples.length > 0 && (
        <SkillExamplesPicker
          examples={examples}
          onPickExample={(ex) => onPickImport(ex.bucket, ex.object, ex.label)}
          layout="canvas"
        />
      )}
      {bucketBrowser?.bucket && (
        <div className="mt-4">
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {bucketBrowser.label || "Library"}
          </p>
          <GCSFileBrowser
            bucket={bucketBrowser.bucket ?? ""}
            rootPath={bucketBrowser.rootPath ?? ""}
            onPick={(bucket, object, label) => onPickImport(bucket, object, label)}
          />
        </div>
      )}
    </div>
  );
}
