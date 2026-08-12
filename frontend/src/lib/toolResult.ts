// Tool-result unwrapping shared across the Activity panel and the workbench
// render hooks. ADK/AG-UI deliver a FunctionTool's return value wrapped and
// often double-encoded — typically `{"result": "{\"doc_id\": …}"}` (one JSON
// string nested in another, under a single `result` key). Consumers that want
// the underlying typed object (to render cards/panels) must undo every layer,
// or a top-level key lookup like `"doc_id" in parsed` fails against the
// envelope. Keep this the single source of truth so surfaces don't drift.

function tryParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

/**
 * Recursively parse stringified-JSON values so nested / double-encoded tool
 * payloads become real structure instead of escaped `\"…\"` blobs. Bounded depth.
 */
export function deepUnwrap(value: unknown, depth = 0): unknown {
  if (depth > 6) return value;
  if (typeof value === "string") {
    const t = value.trim();
    if ((t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"))) {
      const parsed = tryParse(t);
      if (parsed !== undefined) return deepUnwrap(parsed, depth + 1);
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((v) => deepUnwrap(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = deepUnwrap(v, depth + 1);
    return out;
  }
  return value;
}

/**
 * Parse a tool call's `resultContent` into its underlying object, undoing the
 * outer JSON, any double-encoded string bodies, and the single-key
 * `{result: …}` envelope. Returns null when the payload isn't a JSON object
 * (e.g. an offload pointer string or a partial stream).
 */
export function unwrapToolResult(resultContent: string | undefined): Record<string, unknown> | null {
  if (!resultContent) return null;
  const parsed = tryParse(resultContent);
  if (parsed === undefined) return null;
  let top = deepUnwrap(parsed);
  if (top && typeof top === "object" && !Array.isArray(top)) {
    const keys = Object.keys(top);
    if (keys.length === 1 && keys[0] === "result") top = (top as Record<string, unknown>).result;
  }
  return top && typeof top === "object" && !Array.isArray(top) ? (top as Record<string, unknown>) : null;
}
