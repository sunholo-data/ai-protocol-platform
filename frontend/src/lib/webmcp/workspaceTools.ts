/** Experimental WebMCP boundary. Never return source content or credentials. */
export interface WebMCPTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations: { readOnlyHint: boolean };
  execute: (input: Record<string, unknown>) => Promise<{ content: { type: "text"; text: string }[] }>;
}
export interface ModelContext {
  registerTool: (tool: WebMCPTool, options: { signal: AbortSignal }) => void | Promise<void>;
}
export interface WorkspaceSnapshot {
  /** Internal only: invalidates all references on identity/skill/session change. */
  identity: string;
  running: boolean;
  surfaces: { id: string; registered: boolean; mounted: boolean | null; visible: boolean | null; revision: number }[];
}

export function getModelContext(doc: Document): ModelContext | null {
  const candidate = (doc as Document & { modelContext?: ModelContext }).modelContext;
  return candidate && typeof candidate.registerTool === "function" ? candidate : null;
}

export function registerWorkspaceTools(
  api: ModelContext,
  read: () => WorkspaceSnapshot | null,
  focus: (surfaceId: string) => void,
  onError: () => void = () => {},
): () => void {
  let active = true;
  let identity: string | undefined;
  let contextRef = "";
  let refs = new Map<string, string>();
  const controller = new AbortController();
  const stop = () => {
    active = false;
    controller.abort();
  };
  const definitions = [
    ["get_workspace_state", "Read sanitised workspace lifecycle facts. No content or pixel inspection.", true],
    ["inspect_surface", "Inspect lifecycle and DOM visibility of a surface reference. This does not inspect pixels.", true],
    ["focus_asset", "Focus a current workbench surface using its reference. Read state again to check visibility.", false],
  ] as const;
  try {
    for (const [name, description, readOnlyHint] of definitions) {
      const registration = api.registerTool({
        name, description, annotations: { readOnlyHint },
        inputSchema: name === "get_workspace_state"
          ? { type: "object", properties: {}, additionalProperties: false }
          : { type: "object", properties: { contextRef: { type: "string" }, surfaceRef: { type: "string" } }, required: ["contextRef", "surfaceRef"], additionalProperties: false },
        execute: async (input) => {
          const result = (() => {
            const snapshot = active ? read() : null;
            if (!snapshot) return { error: "diagnostics_disabled" };
            if (identity !== snapshot.identity) {
              identity = snapshot.identity;
              contextRef = crypto.randomUUID();
              refs = new Map();
            }
            const currentIds = new Set(snapshot.surfaces.map((surface) => surface.id));
            for (const id of refs.keys()) if (!currentIds.has(id)) refs.delete(id);
            const surfaces = snapshot.surfaces.map((surface) => {
              if (!refs.has(surface.id)) refs.set(surface.id, crypto.randomUUID());
              return { surfaceRef: refs.get(surface.id)!, registered: surface.registered, mounted: surface.mounted, visible: surface.visible, revision: surface.revision, visualInspection: "not_performed" };
            });
            if (name === "get_workspace_state") return { contextRef, running: snapshot.running, surfaces };
            if (!input || input.contextRef !== contextRef) return { error: "stale_context" };
            const index = surfaces.findIndex((surface) => surface.surfaceRef === input.surfaceRef);
            if (index < 0) return { error: "surface_not_found" };
            if (name === "inspect_surface") return surfaces[index];
            if (snapshot.running) return { error: "run_in_progress" };
            focus(snapshot.surfaces[index].id);
            return { status: "focus_requested" };
          })();
          return { content: [{ type: "text", text: JSON.stringify(result) }] };
        },
      }, { signal: controller.signal });
      // Native registration is asynchronous; a rejected promise must revoke all
      // sibling tools and notify the UI rather than leave a partial toolset.
      void Promise.resolve(registration).catch(() => {
        if (!active) return;
        stop();
        onError();
      });
    }
  } catch (error) { stop(); throw error; }
  return stop;
}
