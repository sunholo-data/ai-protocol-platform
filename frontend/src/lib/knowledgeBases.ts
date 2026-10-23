"use client";

/**
 * Knowledge-base calls shared by My Documents (folder menu) and Skill Studio.
 *
 * The access rule lives on the server (`check_binding`): a skill may not reach
 * more people than a knowledge base it searches. `checkBinding` is the dry run
 * of the exact check the skill save runs, so the UI never promises what the
 * save would refuse. Every call returns a result the caller MUST render.
 */

import { fetchWithAuth } from "@/lib/apiClient";
import { notifyDocumentsChanged } from "@/lib/documentEvents";

export const KNOWLEDGE_TOOL = "knowledge_search";

/** `toolConfigs.knowledge_search.include_my_folders`: each user's chat also
 * searches the folders THEY made searchable — no skill edit, no audience check
 * (a caller only ever reaches their own folders). */
export const MY_FOLDERS_KEY = "include_my_folders";

/** Does this skill search every user's own searchable folders? */
export function searchesMyFolders(meta: { tools?: string[]; toolConfigs?: Record<string, unknown> } | undefined): boolean {
  const cfg = (meta?.toolConfigs?.[KNOWLEDGE_TOOL] ?? {}) as Record<string, unknown>;
  return (meta?.tools ?? []).includes(KNOWLEDGE_TOOL) && cfg[MY_FOLDERS_KEY] === true;
}

export interface BindingVerdict {
  ref: string;
  ok: boolean;
  reason: string;
  kbId?: string;
  name?: string;
  audience?: string;
  /** The access the knowledge base would need for this skill, when one would do. */
  widenTo?: Record<string, unknown>;
  /** The caller may change that knowledge base's access. */
  canWiden?: boolean;
}

export type Result<T> = { ok: true; value: T } | { ok: false; message: string };

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const d = body?.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object" && typeof (d as { message?: unknown }).message === "string") {
    return (d as { message: string }).message;
  }
  return `${fallback} (HTTP ${res.status}).`;
}

async function send<T>(url: string, init: RequestInit, fallback: string): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetchWithAuth(url, init);
  } catch {
    return { ok: false, message: `${fallback} (network error).` };
  }
  if (!res.ok) return { ok: false, message: await errorMessage(res, fallback) };
  return { ok: true, value: (await res.json().catch(() => ({}))) as T };
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function checkBinding(
  knowledgeBases: string[],
  accessControl: Record<string, unknown> | undefined,
  ownerId?: string,
): Promise<Result<{ ok: boolean; results: BindingVerdict[] }>> {
  return send(
    "/api/proxy/api/knowledge-bases/_check",
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ knowledgeBases, accessControl: accessControl ?? { type: "private" }, ownerId: ownerId ?? "" }),
    },
    "Couldn't check access",
  );
}

export async function setKnowledgeBaseAccess(kbId: string, accessControl: Record<string, unknown>): Promise<Result<unknown>> {
  const r = await send(
    `/api/proxy/api/knowledge-bases/${encodeURIComponent(kbId)}`,
    { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ accessControl }) },
    "Couldn't change who can search it",
  );
  if (r.ok) notifyDocumentsChanged();
  return r;
}

/** The skill's toolConfigs with `kbId` bound (and the tool enabled). Pure. */
export function withKnowledgeBase(
  meta: { tools?: string[]; toolConfigs?: Record<string, unknown> } | undefined,
  kbId: string,
): { tools: string[]; toolConfigs: Record<string, unknown> } {
  const tools = [...(meta?.tools ?? [])];
  if (!tools.includes(KNOWLEDGE_TOOL)) tools.push(KNOWLEDGE_TOOL);
  const configs = { ...(meta?.toolConfigs ?? {}) };
  const current = (configs[KNOWLEDGE_TOOL] ?? {}) as { knowledge_bases?: unknown };
  const bound = Array.isArray(current.knowledge_bases) ? (current.knowledge_bases as string[]) : [];
  configs[KNOWLEDGE_TOOL] = { ...current, knowledge_bases: bound.includes(kbId) ? bound : [...bound, kbId] };
  return { tools, toolConfigs: configs };
}

/** Bind a knowledge base to an existing skill through the normal skill update
 * (so every skill-route permission and the access rule apply unchanged). */
export async function addKnowledgeBaseToSkill(skillId: string, kbId: string): Promise<Result<unknown>> {
  const got = await send<{ skillMetadata?: Record<string, unknown> }>(
    `/api/proxy/api/skills/${encodeURIComponent(skillId)}`,
    { method: "GET" },
    "Couldn't load the skill",
  );
  if (!got.ok) return got;
  const meta = got.value.skillMetadata ?? {};
  const patch = withKnowledgeBase(meta as { tools?: string[]; toolConfigs?: Record<string, unknown> }, kbId);
  return send(
    `/api/proxy/api/skills/${encodeURIComponent(skillId)}`,
    { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ skillMetadata: { ...meta, ...patch } }) },
    "Couldn't add it to the skill",
  );
}

export interface ForkedSkill {
  skillId: string;
  slug?: string | null;
  ownerId: string;
  displayName?: string;
  name?: string;
}

/** Fork a skill into a private copy the caller owns (`POST /api/skills/{id}/fork`). */
export function forkSkill(skillId: string): Promise<Result<ForkedSkill>> {
  return send<ForkedSkill>(
    `/api/proxy/api/skills/${encodeURIComponent(skillId)}/fork`,
    { method: "POST" },
    "Couldn't make your own copy of the skill",
  );
}
