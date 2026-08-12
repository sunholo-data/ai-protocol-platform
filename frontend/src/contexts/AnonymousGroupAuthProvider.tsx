"use client";

/**
 * AnonymousGroupAuthProvider — fourth auth mode (sprint 2.11, M3).
 *
 * Mounts when `NEXT_PUBLIC_AUTH_MODE=anonymous_group_id`. Wraps the
 * short-code session-join flow:
 *
 *   1. User lands on `/group` and types a code.
 *   2. ``join(code)`` POSTs to ``/api/proxy/api/auth/group/join``.
 *   3. On success: token + uid + expires_at land in sessionStorage;
 *      provider transitions to ``joined`` state and exposes a
 *      Firebase-User-shaped object so downstream consumers
 *      (``useAuth().user``) don't have to branch on mode.
 *   4. On 401 from any downstream call (e.g. revocation),
 *      ``markExpired()`` flips to ``expired`` so the UI can offer
 *      a "ask your teacher for a new code" path.
 *
 * State machine: idle → joining → joined → expired → idle (on clear).
 *
 * Storage: ``sessionStorage`` (NOT localStorage). Closing the tab
 * discards the session — intentional for anonymous deployments.
 *
 * The provider doesn't import the platform's main ``AuthProvider``;
 * it's selected by ``AuthContext.tsx`` based on the env var. Tests
 * mount it directly.
 */

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import {
  clearStoredGroupSession,
  normalizeGroupCode,
  type PersistedGroupSession,
  readStoredGroupSession,
  writeStoredGroupSession,
} from "@/lib/anonymousGroupAuth";

// ─── Public types ───────────────────────────────────────────────────────────

export type GroupAuthStatus = "idle" | "joining" | "joined" | "expired";

export type GroupAuthError =
  | { kind: "unknown_or_revoked"; message: string }
  | { kind: "rate_limited"; message: string; retryAfterSeconds: number }
  | { kind: "at_capacity"; message: string }
  | { kind: "network"; message: string };

/** Shape returned to UI consumers — mirrors `useAuth().user` so the
 * chat-page render tree doesn't branch on auth mode. */
export interface GroupAuthUser {
  uid: string;
  email: ""; // explicit empty — no PII
  displayName: null;
  photoURL: null;
}

export interface AnonymousGroupAuthContextValue {
  status: GroupAuthStatus;
  user: GroupAuthUser | null;
  token: string | null;
  expiresAt: number | null;
  error: GroupAuthError | null;
  join: (groupCode: string) => Promise<void>;
  markExpired: () => void;
  clearStoredToken: () => void;
}

// ─── Internal helpers ───────────────────────────────────────────────────────

function userFromSession(session: PersistedGroupSession): GroupAuthUser {
  return {
    uid: session.uid,
    email: "",
    displayName: null,
    photoURL: null,
  };
}

function classifyError(status: number, body: { detail?: string } | null): GroupAuthError {
  if (status === 429) {
    const detail = body?.detail ?? "rate limit exceeded";
    // Backend includes "retry after Ns" in the detail; extract.
    const match = /retry after (\d+)\s*s/i.exec(detail);
    return {
      kind: "rate_limited",
      message: detail,
      retryAfterSeconds: match ? Number(match[1]) : 60,
    };
  }
  if (status === 503) {
    return { kind: "at_capacity", message: body?.detail ?? "group at capacity" };
  }
  if (status === 401) {
    return {
      kind: "unknown_or_revoked",
      message: body?.detail ?? "group not found or no longer active",
    };
  }
  return {
    kind: "network",
    message: body?.detail ?? `request failed (${status})`,
  };
}

// ─── Context ────────────────────────────────────────────────────────────────

const AnonymousGroupAuthContext = createContext<AnonymousGroupAuthContextValue | null>(
  null,
);

export function AnonymousGroupAuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<GroupAuthStatus>("idle");
  const [session, setSession] = useState<PersistedGroupSession | null>(null);
  const [error, setError] = useState<GroupAuthError | null>(null);

  // Re-hydrate from sessionStorage on first mount (e.g. page refresh in
  // the same tab — token is still valid until expires_at).
  useEffect(() => {
    const stored = readStoredGroupSession();
    if (stored) {
      setSession(stored);
      setStatus("joined");
    }
  }, []);

  const join = useCallback(async (groupCode: string) => {
    const code = normalizeGroupCode(groupCode);
    if (!code) {
      const e: GroupAuthError = {
        kind: "unknown_or_revoked",
        message: "Please enter a group code.",
      };
      setError(e);
      throw new Error(e.message);
    }
    setStatus("joining");
    setError(null);
    try {
      const resp = await fetch("/api/proxy/api/auth/group/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: code }),
      });
      if (!resp.ok) {
        let body: { detail?: string } | null = null;
        try {
          body = (await resp.json()) as { detail?: string };
        } catch {
          // Body wasn't JSON — fall through with null.
        }
        const e = classifyError(resp.status, body);
        setError(e);
        setStatus("idle");
        throw new Error(e.message);
      }
      const data = (await resp.json()) as PersistedGroupSession;
      writeStoredGroupSession(data);
      setSession(data);
      setStatus("joined");
    } catch (err) {
      if (status === "joining") {
        // network-level failure — surface generically.
        const e: GroupAuthError =
          error ?? {
            kind: "network",
            message: err instanceof Error ? err.message : String(err),
          };
        setError(e);
        setStatus("idle");
      }
      throw err;
    }
  }, [status, error]);

  const markExpired = useCallback(() => {
    setStatus("expired");
  }, []);

  const clearStoredToken = useCallback(() => {
    clearStoredGroupSession();
    setSession(null);
    setError(null);
    setStatus("idle");
  }, []);

  const value: AnonymousGroupAuthContextValue = {
    status,
    user: session ? userFromSession(session) : null,
    token: session?.token ?? null,
    expiresAt: session?.expires_at ?? null,
    error,
    join,
    markExpired,
    clearStoredToken,
  };

  return (
    <AnonymousGroupAuthContext.Provider value={value}>
      {children}
    </AnonymousGroupAuthContext.Provider>
  );
}

export function useAnonymousGroupAuth(): AnonymousGroupAuthContextValue {
  const ctx = useContext(AnonymousGroupAuthContext);
  if (!ctx) {
    throw new Error(
      "useAnonymousGroupAuth must be used within an AnonymousGroupAuthProvider",
    );
  }
  return ctx;
}
