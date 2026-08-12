import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clearPendingSkillSwitch,
  emitSkillSwitchIntent,
  readPendingSkillSwitch,
  stashPendingSkillSwitch,
  subscribeSkillSwitchIntent,
  type PendingSkillSwitch,
} from "@/lib/skillSwitch";

const SAMPLE: PendingSkillSwitch = {
  threadId: "thread-1",
  targetSkillId: "26124699-f558-4096-a4a8-a9f73f27eb26",
  prompt: "analyze the obligations in this PPA",
  documentIds: ["doc-a", "doc-b"],
};

afterEach(() => {
  clearPendingSkillSwitch();
});

describe("skill-switch intent bus", () => {
  it("delivers the target skill id to subscribers and stops after unsubscribe", () => {
    const fn = vi.fn();
    const off = subscribeSkillSwitchIntent(fn);
    emitSkillSwitchIntent({ targetSkillId: "s1" });
    expect(fn).toHaveBeenCalledWith({ targetSkillId: "s1" });
    off();
    emitSkillSwitchIntent({ targetSkillId: "s2" });
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe("pending skill-switch stash (survives navigation)", () => {
  it("round-trips a valid stash", () => {
    stashPendingSkillSwitch(SAMPLE);
    expect(readPendingSkillSwitch()).toEqual(SAMPLE);
  });

  it("clears the stash so a refresh cannot re-fire the switch", () => {
    stashPendingSkillSwitch(SAMPLE);
    clearPendingSkillSwitch();
    expect(readPendingSkillSwitch()).toBeNull();
  });

  it("returns null when nothing is stashed", () => {
    expect(readPendingSkillSwitch()).toBeNull();
  });

  it("rejects a corrupt / partial stash rather than returning a half object", () => {
    sessionStorage.setItem("aitana:pending-skill-switch", JSON.stringify({ threadId: "t", targetSkillId: "s" }));
    expect(readPendingSkillSwitch()).toBeNull(); // missing prompt + documentIds
    sessionStorage.setItem("aitana:pending-skill-switch", "not json");
    expect(readPendingSkillSwitch()).toBeNull();
  });

  it("filters non-string document ids defensively", () => {
    sessionStorage.setItem(
      "aitana:pending-skill-switch",
      JSON.stringify({ ...SAMPLE, documentIds: ["ok", 42, null, "fine"] }),
    );
    expect(readPendingSkillSwitch()?.documentIds).toEqual(["ok", "fine"]);
  });
});
