// CLAUDE.md #9 — friendly tool names: catalog → framework map → humanise.

import { describe, expect, it } from "vitest";
import { FRAMEWORK_TOOL_LABELS, humaniseToolName, toolLabel, toolStageLabel } from "@/lib/toolLabels";

describe("toolLabel", () => {
  it("prefers the server catalog label", () => {
    expect(toolLabel("my_context", { my_context: "Your setup" })).toBe("Your setup");
    expect(toolLabel("knowledge_search", { knowledge_search: "Search knowledge bases" })).toBe(
      "Search knowledge bases",
    );
  });

  it("lets the catalog override the static framework map", () => {
    expect(toolLabel("web_search_agent", { web_search_agent: "Search the web" })).toBe("Search the web");
  });

  it("uses the static map for framework / agent tools not in the catalog", () => {
    expect(toolLabel("web_search_agent")).toBe("Web search");
    expect(toolLabel("enterprise_search_agent", {})).toBe("Library search");
    expect(toolLabel("transfer_to_agent")).toBe("Hand over to another skill");
    expect(toolLabel("load_artifacts")).toBe("Open a saved result");
    expect(toolLabel("retrieve_artifact")).toBe("Open a saved result");
    expect(toolLabel("send_a2ui_json_to_client")).toBe("Show a form");
    expect(toolLabel("request_confirmation")).toBe("Ask to confirm");
    expect(toolLabel("load_memory")).toBe("Recall earlier conversations");
    expect(toolLabel("preload_memory")).toBe("Recall earlier conversations");
    expect(toolLabel("search_documents")).toBe("Search your documents");
  });

  it("covers every FRAMEWORK_GATED_TOOLS name from the backend seed", () => {
    // backend/scripts/seed_tool_permissions.py FRAMEWORK_GATED_TOOLS
    for (const name of [
      "load_artifacts",
      "retrieve_artifact",
      "load_memory",
      "preload_memory",
      "search_documents",
      "send_a2ui_json_to_client",
      "request_confirmation",
    ]) {
      expect(FRAMEWORK_TOOL_LABELS[name]).toBeTruthy();
    }
  });

  it("humanises unknown names (e.g. a delegate AgentTool named after a skill slug)", () => {
    expect(toolLabel("demo_research_worker")).toBe("Demo research worker");
    expect(toolLabel("demo-bigquery", null)).toBe("Demo bigquery");
    expect(toolLabel("bq_market_query", { other: "Other" })).toBe("Bq market query");
  });

  it("never returns an empty label", () => {
    expect(toolLabel("")).toBe("Tool");
    expect(toolLabel("__")).toBe("Tool");
  });
});

describe("humaniseToolName", () => {
  it("converts snake / kebab case to sentence case", () => {
    expect(humaniseToolName("UPPER_CASE_TOOL")).toBe("Upper case tool");
    expect(humaniseToolName("kebab-case--tool")).toBe("Kebab case tool");
  });
});

describe("toolStageLabel", () => {
  it("returns a main-chat stage label for search tools only", () => {
    expect(toolStageLabel("web_search_agent")).toBe("Searching the web…");
    expect(toolStageLabel("google_search")).toBe("Searching the web…");
    expect(toolStageLabel("enterprise_search_agent")).toBe("Searching the library…");
    expect(toolStageLabel("ai_search")).toBe("Searching the library…");
    expect(toolStageLabel("list_documents")).toBeNull();
  });
});
