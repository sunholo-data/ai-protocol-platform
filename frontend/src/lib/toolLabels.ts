// Friendly tool names — the tenant frontend source of tool labels.
//
// CLAUDE.md #9 (FRIENDLY NAMES, NOT RAW IDS): raw tool names such as
// `knowledge_search` or `transfer_to_agent` are backend addressing. The chat
// tool chip, the typing indicator and the Activity tab show a human label
// instead, resolved in this order:
//
//   1. the server catalog (`GET /api/tools` — `TOOL_CATALOG` in
//      backend/adk/tools.py plus private plugin catalogs), when loaded;
//   2. FRAMEWORK_TOOL_LABELS below — tools the agent factory wires itself
//      (AgentTools, ADK built-ins, elicitation) that are not in the catalog;
//   3. humanise(name) — `demo_research_worker` → "Demo research worker". A
//      delegate AgentTool is named after a skill slug, so this reads fine.
//
// The raw name stays available as secondary detail (tooltip / expanded view).

/** name → label, as served by `GET /api/tools`. */
export type ToolCatalogLabels = Record<string, string>;

/**
 * Labels for framework / agent tools NOT in the server catalog. Keep in step
 * with FRAMEWORK_GATED_TOOLS + the AgentTool names in
 * backend/scripts/seed_tool_permissions.py.
 */
export const FRAMEWORK_TOOL_LABELS: Readonly<Record<string, string>> = {
  web_search_agent: "Web search",
  enterprise_search_agent: "Library search",
  transfer_to_agent: "Hand over to another skill",
  request_handoff: "Hand over to another skill",
  load_artifacts: "Open a saved result",
  retrieve_artifact: "Open a saved result",
  send_a2ui_json_to_client: "Show a form",
  request_confirmation: "Ask to confirm",
  load_memory: "Recall earlier conversations",
  preload_memory: "Recall earlier conversations",
  search_documents: "Search your documents",
};

/**
 * Progress text shown in the main chat while a search tool runs (#12: search
 * runs as an AgentTool / native grounding with no handoff chip, so a stage
 * label keeps it visible). Non-search tools have no stage label.
 */
const SEARCH_STAGE_LABELS: Readonly<Record<string, string>> = {
  ai_search: "Searching the library…",
  enterprise_search_agent: "Searching the library…",
  knowledge_search: "Searching the library…",
  google_search: "Searching the web…",
  web_search_agent: "Searching the web…",
};

/** snake_case / kebab-case → "Sentence case". Empty input → "Tool". */
export function humaniseToolName(name: string): string {
  const words = name
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  if (!words) return "Tool";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Friendly label for a tool name: catalog → framework map → humanise. */
export function toolLabel(name: string, catalog?: ToolCatalogLabels | null): string {
  const fromCatalog = catalog?.[name];
  if (fromCatalog) return fromCatalog;
  const framework = FRAMEWORK_TOOL_LABELS[name];
  if (framework) return framework;
  return humaniseToolName(name);
}

/** Stage label for the main-chat status while `name` runs, or null. */
export function toolStageLabel(name: string): string | null {
  return SEARCH_STAGE_LABELS[name] ?? null;
}
