import { describe, expect, it } from "vitest";
import { skillHref } from "../skillHref";

describe("skillHref", () => {
  it("uses friendly URL when slug + ownerId are present", () => {
    expect(skillHref({ skillId: "uuid-1", slug: "research", ownerId: "uid-mark" })).toBe(
      "/chat/@uid-mark/research",
    );
  });

  it("falls back to UUID when slug is missing", () => {
    expect(skillHref({ skillId: "uuid-1", slug: null, ownerId: "uid-mark" })).toBe("/chat/uuid-1");
  });

  it("falls back to UUID when slug is empty string", () => {
    expect(skillHref({ skillId: "uuid-1", slug: "", ownerId: "uid-mark" })).toBe("/chat/uuid-1");
  });

  it("encodes slug + ownerId for URL safety", () => {
    expect(skillHref({ skillId: "uuid-1", slug: "weird/name", ownerId: "uid mark" })).toBe(
      "/chat/@uid%20mark/weird%2Fname",
    );
  });
});
