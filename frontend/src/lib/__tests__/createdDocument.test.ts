// v6.34.0 DOC-WORKBENCH P1 — the created document mounts itself.
//
// The two decisions under test (2026-09-01):
//   1. ONE TAB — an openable document becomes a Document tab and does NOT also
//      get a "Result" tab.
//   2. A FAILED generation has no docId, so there is nothing to open; it keeps
//      its card, because a card is all that stands between the user and a silent
//      non-event (principle #8).
//
// Plus the blink rule inherited from `workbenchFocus`: a surface that drops out
// of `listArtifacts()` during re-registration and comes back must NOT read as a
// fresh arrival — otherwise it reopens a document the user navigated away from.

import { describe, it, expect } from "vitest";
import {
  artifactsExcludingOpenableDocuments,
  documentIdFromSurfaceId,
  formatFromFilename,
  isOpenableDocumentArtifact,
  newlyCreatedDocuments,
} from "../createdDocument";

const doc = (id: string, title = "Offer.docx") => ({
  surfaceId: `created_document:${id}`,
  kind: "document",
  title,
});
const failedDoc = (title = "Offer.docx") => ({ surfaceId: "created_document", kind: "document", title });
const otherArtifact = (id: string) => ({ surfaceId: id, kind: "prices", title: "Prices" });

describe("documentIdFromSurfaceId", () => {
  it("extracts the docId the backend encoded in the surfaceId", () => {
    expect(documentIdFromSurfaceId("created_document:abc-123")).toBe("abc-123");
  });

  it("returns empty for the bare failure surface", () => {
    expect(documentIdFromSurfaceId("created_document")).toBe("");
  });

  it("returns empty for an unrelated surface", () => {
    expect(documentIdFromSurfaceId("bigquery_result:xyz")).toBe("");
  });

  it("handles a deployed UUID doc-id, not just a short fixture id", () => {
    const uuid = "26124699-f558-4a1b-9c33-000000000000";
    expect(documentIdFromSurfaceId(`created_document:${uuid}`)).toBe(uuid);
  });
});

describe("formatFromFilename", () => {
  it.each([
    ["Offer.docx", "docx"],
    ["deck.PPTX", "pptx"],
    ["no-extension", ""],
    ["trailing.", ""],
    [".hidden", ""],
    ["Offer - Q3 2026.final.docx", "docx"],
  ])("%s → %s", (name, expected) => {
    expect(formatFromFilename(name)).toBe(expected);
  });
});

describe("decision 1 — one tab per document", () => {
  it("removes an openable document from the workbench tab list", () => {
    const artifacts = [otherArtifact("prices:1"), doc("d1")];
    const tabs = artifactsExcludingOpenableDocuments(artifacts);
    expect(tabs.map((a) => a.surfaceId)).toEqual(["prices:1"]);
  });

  it("leaves every other artifact kind alone", () => {
    const artifacts = [otherArtifact("prices:1"), otherArtifact("sources")];
    expect(artifactsExcludingOpenableDocuments(artifacts)).toHaveLength(2);
  });
});

describe("decision 2 — a failed generation keeps its card", () => {
  it("is not openable", () => {
    expect(isOpenableDocumentArtifact(failedDoc())).toBe(false);
  });

  it("still gets a workbench tab, so the error is visible", () => {
    const tabs = artifactsExcludingOpenableDocuments([failedDoc()]);
    expect(tabs).toHaveLength(1);
  });

  it("is never opened as a document", () => {
    expect(newlyCreatedDocuments([failedDoc()], new Set())).toEqual([]);
  });
});

describe("newlyCreatedDocuments", () => {
  it("returns a document ready for handleDocClick", () => {
    expect(newlyCreatedDocuments([doc("d1", "Offer - Northwind.docx")], new Set())).toEqual([
      { id: "d1", filename: "Offer - Northwind.docx", format: "docx" },
    ]);
  });

  it("opens each document only once", () => {
    const seen = new Set<string>();
    const artifacts = [doc("d1")];
    expect(newlyCreatedDocuments(artifacts, seen)).toHaveLength(1);
    expect(newlyCreatedDocuments(artifacts, seen)).toHaveLength(0);
  });

  it("does NOT reopen a document that blinked out and came back", () => {
    // listArtifacts() drops a surface while it re-registers. Comparing against
    // the previous render would read the return as a fresh arrival and yank the
    // user back — the v6.23.0 B4 bug, in a new place.
    const seen = new Set<string>();
    newlyCreatedDocuments([doc("d1")], seen);
    newlyCreatedDocuments([], seen); // blink out
    expect(newlyCreatedDocuments([doc("d1")], seen)).toEqual([]);
  });

  it("opens a genuinely different document", () => {
    const seen = new Set<string>();
    newlyCreatedDocuments([doc("d1")], seen);
    const next = newlyCreatedDocuments([doc("d1"), doc("d2", "Memo.docx")], seen);
    expect(next.map((d) => d.id)).toEqual(["d2"]);
  });

  it("preserves order so the LAST document created ends up focused", () => {
    const out = newlyCreatedDocuments([doc("d1", "A.docx"), doc("d2", "B.docx")], new Set());
    expect(out.map((d) => d.id)).toEqual(["d1", "d2"]);
  });

  it("falls back to a sane name when the artifact has no title", () => {
    const out = newlyCreatedDocuments([{ surfaceId: "created_document:d9", kind: "document" }], new Set());
    expect(out[0]).toEqual({ id: "d9", filename: "Document", format: "" });
  });

  it("ignores artifacts that are not documents", () => {
    expect(newlyCreatedDocuments([otherArtifact("prices:1")], new Set())).toEqual([]);
  });
});
