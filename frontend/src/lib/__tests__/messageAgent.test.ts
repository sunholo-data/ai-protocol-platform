import { describe, it, expect } from "vitest";
import { resolveMessageAgent, buildAgentMap, type AgentDelegation } from "../messageAgent";

const ROOT = { avatar: "/root.svg", label: null };

function deleg(over: Partial<AgentDelegation>): AgentDelegation {
  return { afterMessageId: null, targetDisplay: "X", avatar: "/x.svg", mode: "auto", ...over };
}

describe("resolveMessageAgent (6.11)", () => {
  // Transcript: u1, a1(root), u2, [delegate→Web Researcher], a2(WR), u3, [delegate→PPA], a3(PPA)
  const ids = ["u1", "a1", "u2", "a2", "u3", "a3"];
  const delegations: AgentDelegation[] = [
    deleg({ afterMessageId: "u2", targetDisplay: "Web Researcher", avatar: "/wr.svg" }),
    deleg({ afterMessageId: "u3", targetDisplay: "Contract Expert", avatar: "/ppa.svg" }),
  ];

  it("root skill answers before any delegation", () => {
    expect(resolveMessageAgent("a1", ids, delegations, ROOT)).toEqual(ROOT);
  });

  it("attributes a message to the delegate whose handoff precedes it", () => {
    expect(resolveMessageAgent("a2", ids, delegations, ROOT)).toEqual({ avatar: "/wr.svg", label: "Web Researcher" });
    expect(resolveMessageAgent("a3", ids, delegations, ROOT)).toEqual({ avatar: "/ppa.svg", label: "Contract Expert" });
  });

  it("the latest preceding handoff wins (avatar changes as it swaps)", () => {
    // a3 comes after both handoffs → the most recent (PPA) applies, not Web Researcher.
    expect(resolveMessageAgent("a3", ids, delegations, ROOT).label).toBe("Contract Expert");
  });

  it("ignores suggest-mode proposals (no actual handoff)", () => {
    const suggest = [deleg({ afterMessageId: "u2", targetDisplay: "Proposed", avatar: "/p.svg", mode: "suggest" })];
    expect(resolveMessageAgent("a2", ids, suggest, ROOT)).toEqual(ROOT);
  });

  it("ignores a delegation whose anchor is unknown / null", () => {
    const orphan = [deleg({ afterMessageId: "missing", targetDisplay: "Ghost" })];
    expect(resolveMessageAgent("a2", ids, orphan, ROOT)).toEqual(ROOT);
  });

  it("unknown message id → root", () => {
    expect(resolveMessageAgent("nope", ids, delegations, ROOT)).toEqual(ROOT);
  });

  it("buildAgentMap covers every id", () => {
    const map = buildAgentMap(ids, delegations, ROOT);
    expect(map.get("a1")).toEqual(ROOT);
    expect(map.get("a2")?.label).toBe("Web Researcher");
    expect(map.get("a3")?.label).toBe("Contract Expert");
    expect(map.size).toBe(ids.length);
  });
});
