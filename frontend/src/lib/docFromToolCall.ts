// #11: when the AI reads/analyses a document via a tool, open it in the document
// bar — matching a manual selection. These tools carry the document identity in
// their args; extracting it lets ChatShell route the doc through the SAME
// import-by-reference path a manual pick uses (parses + opens + focuses a tab).
//
// A bucket `gs://` URL is opened fresh (parse + tab); a bare `doc_id` focuses an
// already-open tab. (An uploaded doc_id the user never opened is a follow-up —
// the bucket path covers the Contracts library, which is gs:// content.)

import type { CompareDocIdentity } from "@/components/workspace/CompareLauncher";

/** Tools whose invocation means "the AI is looking at this document." */
export const DOC_LOADING_TOOLS = new Set([
  "get_document_content",
  "extract_ppa_clauses",
  "map_ppa_obligations",
]);

/**
 * Pull a document identity out of a doc-loading tool call's args, or null if the
 * tool isn't doc-loading / the args aren't (yet) a parseable identity. Safe on
 * partial streamed argsJson — a JSON.parse failure just yields null (skip until
 * the args finish streaming).
 */
export function docIdentityFromToolCall(
  name: string,
  argsJson: string | undefined,
): CompareDocIdentity | null {
  if (!DOC_LOADING_TOOLS.has(name) || !argsJson) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(argsJson);
  } catch {
    return null; // partial stream — not parseable yet
  }
  if (!parsed || typeof parsed !== "object") return null;
  const args = parsed as Record<string, unknown>;
  const gsUrl = typeof args.gs_url === "string" ? args.gs_url : null;
  if (gsUrl && gsUrl.startsWith("gs://")) return { gs_url: gsUrl };
  const docId = typeof args.doc_id === "string" ? args.doc_id.trim() : null;
  if (docId) return { doc_id: docId };
  return null;
}

/** Stable dedupe key for a document identity. */
export function docIdentityKey(identity: CompareDocIdentity): string {
  return "gs_url" in identity ? identity.gs_url : identity.doc_id;
}
