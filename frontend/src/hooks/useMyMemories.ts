"use client";

/**
 * "Your memory" (docs/design/private/memory-screen.md): what the assistant
 * remembers about the signed-in user, personal or one project's (`projectId`).
 * The backend decides the scope from the token; this hook only lists and asks
 * for deletes.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

const API = "/api/proxy/api/me/memories";

export interface MemoryItem {
  ref: string;
  fact: string;
  updatedAt: string | null;
}

export type MemoriesStatus = "loading" | "ready" | "unavailable" | "error";

export type MemoryActionResult = { ok: true } | { ok: false; message: string };

function query(projectId: string | null, extra: Record<string, string> = {}): string {
  const qs = new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), ...extra });
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: { message?: unknown } | string } | null;
  const detail = body?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return fallback;
}

export function useMyMemories(projectId: string | null = null): {
  memories: MemoryItem[];
  status: MemoriesStatus;
  error: string | null;
  truncated: boolean;
  retry: () => void;
  remove: (ref: string) => Promise<MemoryActionResult>;
  removeAll: () => Promise<MemoryActionResult>;
} {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [status, setStatus] = useState<MemoriesStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const res = await fetchWithAuth(`${API}${query(projectId)}`);
      if (!res.ok) throw new Error(await errorMessage(res, `the server answered ${res.status}`));
      const body = (await res.json()) as {
        available?: boolean;
        truncated?: boolean;
        memories?: { ref?: unknown; fact?: unknown; updated_at?: unknown }[];
      };
      if (!body.available) {
        setMemories([]);
        setStatus("unavailable");
        return;
      }
      setMemories(
        (body.memories ?? [])
          .filter((m) => typeof m.ref === "string" && typeof m.fact === "string")
          .map((m) => ({
            ref: m.ref as string,
            fact: m.fact as string,
            updatedAt: typeof m.updated_at === "string" ? m.updated_at : null,
          })),
      );
      setTruncated(Boolean(body.truncated));
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setStatus("error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = useCallback(
    async (ref: string): Promise<MemoryActionResult> => {
      try {
        const res = await fetchWithAuth(`${API}/item${query(projectId, { ref })}`, { method: "DELETE" });
        if (!res.ok) return { ok: false, message: await errorMessage(res, "Couldn't delete that memory. Try again.") };
        setMemories((prev) => prev.filter((m) => m.ref !== ref));
        return { ok: true };
      } catch {
        return { ok: false, message: "Couldn't reach the server. Try again." };
      }
    },
    [projectId],
  );

  const removeAll = useCallback(async (): Promise<MemoryActionResult> => {
    try {
      const res = await fetchWithAuth(`${API}${query(projectId, { confirm: "forget" })}`, { method: "DELETE" });
      if (!res.ok) return { ok: false, message: await errorMessage(res, "Couldn't delete your memory. Try again.") };
      const body = (await res.json().catch(() => ({}))) as { failed?: number };
      await load();
      return body.failed
        ? { ok: false, message: `${body.failed} couldn't be deleted. Try again.` }
        : { ok: true };
    } catch {
      return { ok: false, message: "Couldn't reach the server. Try again." };
    }
  }, [projectId, load]);

  return { memories, status, error, truncated, retry: () => void load(), remove, removeAll };
}
