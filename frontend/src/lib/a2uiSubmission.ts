// Parse an A2UI form-submission chat message (6.11 polish).
//
// When a user submits an A2UI surface form (e.g. the elicitation "Save
// Preferences" button), the frontend sends it to the agent as a chat message
// of the form `[a2ui:<action>] {<json context>}` (see ChatShell handleAction).
// That raw text reads badly in the transcript — this parses it so the bubble
// can render a clean "Submitted …" chip instead.

export interface A2uiSubmission {
  action: string;
  /** Flattened field → display value(s), for a compact summary. */
  fields: { key: string; value: string }[];
}

const RE = /^\[a2ui:([A-Za-z0-9_]+)\]\s*(\{[\s\S]*\})\s*$/;

function humanizeKey(k: string): string {
  return k
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^\w/, (c) => c.toUpperCase());
}

function displayValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(displayValue).join(", ");
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

/** Parse `[a2ui:action] {json}` → structured submission, or null if it isn't one. */
export function parseA2uiSubmission(content: string): A2uiSubmission | null {
  const m = RE.exec(content.trim());
  if (!m) return null;
  const action = m[1];
  let obj: Record<string, unknown>;
  try {
    const parsed = JSON.parse(m[2]);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    obj = parsed as Record<string, unknown>;
  } catch {
    return null;
  }
  const fields = Object.entries(obj)
    .map(([key, v]) => ({ key: humanizeKey(key), value: displayValue(v) }))
    .filter((f) => f.value !== "");
  return { action, fields };
}
