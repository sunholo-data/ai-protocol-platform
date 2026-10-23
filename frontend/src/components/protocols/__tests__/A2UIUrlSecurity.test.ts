import { basicCatalog } from "@a2ui/react/v0_9";
import { afterEach, describe, expect, it, vi } from "vitest";

// Exercise the real catalog used by both A2UI render paths, without SDK mocks.
const openUrl = basicCatalog.functions.get("openUrl")!;
const context = {} as Parameters<typeof openUrl.execute>[1];

afterEach(() => vi.restoreAllMocks());

describe("agent-supplied A2UI URL actions", () => {
  it.each(["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,unsafe", "file:///etc/passwd"])(
    "rejects unsafe scheme: %s",
    (url) => {
      const open = vi.spyOn(window, "open").mockReturnValue(null);
      expect(() => openUrl.execute({ url }, context)).toThrow();
      expect(open).not.toHaveBeenCalled();
    },
  );

  it("opens an HTTPS link without giving the new page an opener", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    openUrl.execute({ url: "https://example.com/physics" }, context);
    expect(open).toHaveBeenCalledWith("https://example.com/physics", "_blank", "noopener,noreferrer");
  });
});
