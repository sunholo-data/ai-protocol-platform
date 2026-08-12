// M2A — adapter that maps MCP App "notify" notifications coming back from the
// guest UI iframe into chat messages we can hand to useSkillAgent.sendMessage.
//
// Known shapes (per the design doc + MCP Apps spec educated guesses; we'll
// extend these as real demo apps surface concrete reasons):
//   - {type:"app/notify", reason:"location-selected",
//      payload:{location:string}}
//   - {type:"app/notify", reason:"route-selected",
//      payload:{from:string, to:string}}
//
// Negative cases must return null without throwing — the parser is a pure
// boundary and the iframe is untrusted. Forward-compatibility: unknown
// reasons fall through (so a future spec extension doesn't crash this build).

import { describe, expect, it } from "vitest";
import { notificationToChatMessage } from "@/components/protocols/mcpAppNotificationAdapter";

describe("notificationToChatMessage — known shapes", () => {
  it("location-selected → 'Tell me more about <location>'", () => {
    const out = notificationToChatMessage({
      type: "app/notify",
      reason: "location-selected",
      payload: { location: "Munich" },
    });
    expect(out).toBe("Tell me more about Munich");
  });

  it("route-selected → 'Tell me about the route from <a> to <b>'", () => {
    const out = notificationToChatMessage({
      type: "app/notify",
      reason: "route-selected",
      payload: { from: "Berlin", to: "Munich" },
    });
    expect(out).toBe("Tell me about the route from Berlin to Munich");
  });
});

describe("notificationToChatMessage — negative shapes", () => {
  it("returns null for unknown reason (forward-compat)", () => {
    expect(
      notificationToChatMessage({
        type: "app/notify",
        reason: "future-event",
        payload: { something: 1 },
      }),
    ).toBeNull();
  });

  it("returns null for unknown type", () => {
    expect(
      notificationToChatMessage({
        type: "app/somethingelse",
        reason: "location-selected",
        payload: { location: "Munich" },
      }),
    ).toBeNull();
  });

  it("returns null for missing payload", () => {
    expect(
      notificationToChatMessage({
        type: "app/notify",
        reason: "location-selected",
      }),
    ).toBeNull();
  });

  it("returns null for missing required field (location)", () => {
    expect(
      notificationToChatMessage({
        type: "app/notify",
        reason: "location-selected",
        payload: {},
      }),
    ).toBeNull();
  });

  it("returns null for non-string field value", () => {
    expect(
      notificationToChatMessage({
        type: "app/notify",
        reason: "location-selected",
        payload: { location: 42 },
      }),
    ).toBeNull();
  });

  it("returns null for null input", () => {
    expect(notificationToChatMessage(null)).toBeNull();
  });

  it("returns null for undefined input", () => {
    expect(notificationToChatMessage(undefined)).toBeNull();
  });

  it("returns null for non-object input (string)", () => {
    expect(notificationToChatMessage("not an object")).toBeNull();
  });

  it("returns null for non-object input (number)", () => {
    expect(notificationToChatMessage(42)).toBeNull();
  });

  it("returns null for empty object", () => {
    expect(notificationToChatMessage({})).toBeNull();
  });

  it("returns null for route-selected missing 'to'", () => {
    expect(
      notificationToChatMessage({
        type: "app/notify",
        reason: "route-selected",
        payload: { from: "Berlin" },
      }),
    ).toBeNull();
  });
});
