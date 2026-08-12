import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ArtefactReview,
  ArtefactReviewer,
  PermissiveArtefactReviewer,
  clearArtefactReviewer,
  getArtefactReviewer,
  setArtefactReviewer,
} from "../ArtefactReviewer";

function makeReview(overrides: Partial<ArtefactReview> = {}): ArtefactReview {
  return {
    toolName: "physics_sim_builder",
    serverId: "demo-mcp-server",
    resourceUri: "ui://render/abc",
    html: "<html><body>hi</body></html>",
    csp: "default-src 'self'",
    structuredContent: { v: 1 },
    invocationId: "inv-test",
    ...overrides,
  };
}

describe("ArtefactReviewer registry", () => {
  afterEach(() => {
    clearArtefactReviewer();
  });

  it("returns the permissive default when nothing is registered", async () => {
    const reviewer = getArtefactReviewer();
    const decision = await reviewer.review(makeReview());
    expect(decision.action).toBe("approve");
  });

  it("setArtefactReviewer replaces the default", async () => {
    const blocker: ArtefactReviewer = {
      async review() {
        return { action: "block", message: "no", reasonCode: "TEST_BLOCK" };
      },
    };
    setArtefactReviewer(blocker);
    const decision = await getArtefactReviewer().review(makeReview());
    expect(decision.action).toBe("block");
    if (decision.action === "block") {
      expect(decision.message).toBe("no");
      expect(decision.reasonCode).toBe("TEST_BLOCK");
    }
  });

  it("clearArtefactReviewer resets to the permissive default", async () => {
    const blocker: ArtefactReviewer = {
      async review() {
        return { action: "block", message: "no", reasonCode: "TEST_BLOCK" };
      },
    };
    setArtefactReviewer(blocker);
    clearArtefactReviewer();
    const decision = await getArtefactReviewer().review(makeReview());
    expect(decision.action).toBe("approve");
  });

  it("PermissiveArtefactReviewer approves any input", async () => {
    const decision = await PermissiveArtefactReviewer.review(
      makeReview({ html: "<script>alert(1)</script>" }),
    );
    expect(decision.action).toBe("approve");
  });

  it("setArtefactReviewer rejects a value that does not satisfy the interface", () => {
    // Plain object without review() — should fail loud.
    expect(() => {
      // @ts-expect-error — intentionally wrong shape
      setArtefactReviewer({});
    }).toThrow(/ArtefactReviewer/);
  });

  it("registering twice replaces the previous reviewer (no warning, no error)", async () => {
    const first = vi.fn().mockResolvedValue({ action: "approve" } as const);
    const second = vi.fn().mockResolvedValue({
      action: "warn" as const,
      message: "heads up",
      reasonCode: "TEST_WARN",
    });
    setArtefactReviewer({ review: first });
    setArtefactReviewer({ review: second });
    const decision = await getArtefactReviewer().review(makeReview());
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    expect(decision.action).toBe("warn");
  });
});

describe("ArtefactDecision discriminated union", () => {
  it("narrows on action === 'block' to the block variant", () => {
    const decision: { action: "block"; message: string; reasonCode: string; appealUrl?: string } = {
      action: "block",
      message: "Contains forbidden tag",
      reasonCode: "FORBIDDEN_TAG",
      appealUrl: "https://example.com/appeal",
    };
    // TypeScript narrowing: this access is only valid because `action === "block"`.
    if (decision.action === "block") {
      expect(decision.reasonCode).toBe("FORBIDDEN_TAG");
      expect(decision.appealUrl).toBe("https://example.com/appeal");
    }
  });

  it("narrows on action === 'warn' to the warn variant", () => {
    const decision: { action: "warn"; message: string; reasonCode: string } = {
      action: "warn",
      message: "Review pending",
      reasonCode: "REVIEW_PENDING",
    };
    if (decision.action === "warn") {
      expect(decision.message).toBe("Review pending");
    }
  });

  it("approve variant has no extra fields", () => {
    const decision: { action: "approve" } = { action: "approve" };
    expect(decision.action).toBe("approve");
  });
});

describe("ArtefactReview shape", () => {
  it("exposes camelCase field names mirroring the Python snake_case fields", () => {
    const review = makeReview();
    expect(review).toMatchObject({
      toolName: expect.any(String),
      serverId: expect.any(String),
      resourceUri: expect.any(String),
      html: expect.any(String),
      structuredContent: expect.anything(),
      invocationId: expect.any(String),
    });
    // csp is nullable per the design spec
    expect(typeof review.csp === "string" || review.csp === null).toBe(true);
  });
});
