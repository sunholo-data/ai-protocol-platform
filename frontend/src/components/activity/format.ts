// Shared activity formatting — the parsing/display helpers behind every
// activity feed (the chat Activity tab and the admin analytics trace). One
// implementation so tool args/results render identically wherever they appear.

"use client";

import { useEffect, useState } from "react";

/** Re-render on an interval so relative timestamps stay fresh without per-row timers. */
export function useNow(intervalMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function formatRelative(ts: number, now: number): string {
  if (!ts) return "";
  const diff = Math.max(0, now - ts);
  const s = Math.floor(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(ts).toLocaleDateString();
}

export function absoluteTime(ts: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString();
}

function tryParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

/**
 * Recursively parse stringified-JSON values so nested / double-encoded tool
 * payloads render as real structure instead of an escaped `\"...\"` blob.
 * Tools often return `{"result": "{\"left\": ...}"}` — one JSON string wrapped
 * in another; this unwinds every such layer (bounded depth).
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

/** A readable inline value for the args key→value list. */
export function displayValue(v: unknown): { text: string; mono: boolean } {
  if (v === null || v === undefined) return { text: "—", mono: false };
  if (typeof v === "string") return { text: v, mono: false };
  if (typeof v === "number" || typeof v === "boolean") return { text: String(v), mono: true };
  if (Array.isArray(v) && v.every((x) => x === null || typeof x !== "object")) {
    return { text: v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", "), mono: false };
  }
  return { text: JSON.stringify(v, null, 2), mono: true };
}

export type ArgsView = { fields: [string, unknown][]; raw: null } | { fields: null; raw: string } | null;

/** Parse streamed args → a flat key→value list when it's a JSON object,
 * else a pretty (unwrapped) block. */
export function parseArgs(argsJson?: string): ArgsView {
  if (!argsJson || !argsJson.trim()) return null;
  const parsed = tryParse(argsJson);
  if (parsed === undefined) return { fields: null, raw: argsJson }; // partial stream
  const unwrapped = deepUnwrap(parsed);
  if (unwrapped && typeof unwrapped === "object" && !Array.isArray(unwrapped)) {
    return { fields: Object.entries(unwrapped), raw: null };
  }
  return { fields: null, raw: JSON.stringify(unwrapped, null, 2) };
}

export type ResultView =
  | { kind: "json"; value: unknown; copyText: string }
  | { kind: "text"; text: string; copyText: string }
  | null;

/** Unwrap a tool result — hoisting the common single-`result` envelope and any
 * nested JSON strings — into either structured data (rendered as a tree) or
 * clean plain text. `copyText` is what the Copy button hands downstream. */
export function formatResult(result?: string): ResultView {
  const r = result?.trim();
  if (!r) return null;
  const parsed = tryParse(r);
  if (parsed === undefined) return { kind: "text", text: r, copyText: r }; // plain text
  let top = deepUnwrap(parsed);
  // Hoist the ubiquitous `{ "result": <payload> }` envelope.
  if (top && typeof top === "object" && !Array.isArray(top)) {
    const keys = Object.keys(top);
    if (keys.length === 1 && keys[0] === "result") top = (top as Record<string, unknown>).result;
  }
  if (top && typeof top === "object") {
    return { kind: "json", value: top, copyText: JSON.stringify(top, null, 2) };
  }
  return { kind: "text", text: String(top), copyText: String(top) };
}
