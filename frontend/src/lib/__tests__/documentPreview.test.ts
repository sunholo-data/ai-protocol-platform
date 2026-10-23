// The stale-preview bug, pinned. Reported 2026-09-04 as "I apply the
// formatting, the page refreshes and nothing changes" — the restyle worked
// every time and the browser redrew its cached copy of the PRE-restyle bytes,
// because the URL never varied while the backend served `max-age=300` on it.

import { describe, it, expect } from "vitest";
import { documentPreviewUrl } from "../documentPreview";

describe("documentPreviewUrl", () => {
  it("carries the freshness token, so re-ingested bytes are not served from cache", () => {
    const before = documentPreviewUrl("doc-1", "2026-09-04T10:00:00Z");
    const after = documentPreviewUrl("doc-1", "2026-09-04T10:05:00Z");
    expect(before).not.toBe(after);
    expect(after).toContain("v=");
  });

  it("omits the token when the document has not been parsed yet", () => {
    // No `v` makes the backend answer `no-cache` — better than pinning a
    // placeholder version to bytes that are about to change.
    expect(documentPreviewUrl("doc-1", "")).toBe("/api/proxy/api/documents/doc-1/preview");
  });

  it("escapes the doc id and the token", () => {
    const url = documentPreviewUrl("a/b", "x y");
    expect(url).toContain("a%2Fb");
    expect(url).not.toContain(" ");
  });
});
