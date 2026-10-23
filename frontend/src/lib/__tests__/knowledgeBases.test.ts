import { describe, expect, it } from "vitest";
import { withKnowledgeBase } from "../knowledgeBases";

describe("withKnowledgeBase", () => {
  it("enables the tool and appends the knowledge base once, keeping other config", () => {
    const once = withKnowledgeBase({ tools: ["google_search"], toolConfigs: { knowledge_search: { knowledge_bases: ["a"], default_filters: { status: "implemented" } }, mcp: { servers: [] } } }, "b");
    expect(once.tools).toEqual(["google_search", "knowledge_search"]);
    expect(once.toolConfigs).toEqual({ knowledge_search: { knowledge_bases: ["a", "b"], default_filters: { status: "implemented" } }, mcp: { servers: [] } });
    expect(withKnowledgeBase(once, "b")).toEqual(once);
  });
  it("works on a skill with no metadata", () => {
    expect(withKnowledgeBase(undefined, "k")).toEqual({ tools: ["knowledge_search"], toolConfigs: { knowledge_search: { knowledge_bases: ["k"] } } });
  });
});
