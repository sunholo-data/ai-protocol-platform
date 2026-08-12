// #11: AI-selected documents → document bar. Unit tests for the identity parser.

import { describe, it, expect } from "vitest";
import { docIdentityFromToolCall, docIdentityKey, DOC_LOADING_TOOLS } from "@/lib/docFromToolCall";

describe("docIdentityFromToolCall", () => {
  it("extracts a gs_url from a bucket read", () => {
    const id = docIdentityFromToolCall("extract_ppa_clauses", '{"gs_url":"gs://one-bucket/PPAs/x.pdf"}');
    expect(id).toEqual({ gs_url: "gs://one-bucket/PPAs/x.pdf" });
  });

  it("extracts a doc_id from get_document_content", () => {
    const id = docIdentityFromToolCall("get_document_content", '{"doc_id":"abc-123"}');
    expect(id).toEqual({ doc_id: "abc-123" });
  });

  it("prefers gs_url when both are present", () => {
    const id = docIdentityFromToolCall("extract_ppa_clauses", '{"doc_id":"abc","gs_url":"gs://b/x.pdf"}');
    expect(id).toEqual({ gs_url: "gs://b/x.pdf" });
  });

  it("ignores non-doc-loading tools", () => {
    expect(docIdentityFromToolCall("ai_search", '{"query":"x"}')).toBeNull();
    expect(docIdentityFromToolCall("list_documents", '{"doc_id":"abc"}')).toBeNull();
  });

  it("returns null on partial / unparseable streamed args", () => {
    expect(docIdentityFromToolCall("get_document_content", '{"doc_id":"ab')).toBeNull();
    expect(docIdentityFromToolCall("get_document_content", undefined)).toBeNull();
  });

  it("returns null when no identity is present", () => {
    expect(docIdentityFromToolCall("map_ppa_obligations", "{}")).toBeNull();
    expect(docIdentityFromToolCall("get_document_content", '{"doc_id":""}')).toBeNull();
  });

  it("rejects a non-gs:// url in gs_url", () => {
    expect(docIdentityFromToolCall("extract_ppa_clauses", '{"gs_url":"https://x/y.pdf"}')).toBeNull();
  });

  it("covers the three doc-loading tools", () => {
    expect([...DOC_LOADING_TOOLS].sort()).toEqual([
      "extract_ppa_clauses",
      "get_document_content",
      "map_ppa_obligations",
    ]);
  });
});

describe("docIdentityKey", () => {
  it("keys by gs_url or doc_id", () => {
    expect(docIdentityKey({ gs_url: "gs://b/x.pdf" })).toBe("gs://b/x.pdf");
    expect(docIdentityKey({ doc_id: "abc" })).toBe("abc");
  });
});
