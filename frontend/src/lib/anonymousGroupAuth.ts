/**
 * Anonymous group-ID auth helpers (sprint 2.11, M3).
 *
 * Mirrors `localMode.ts`'s shape — a tiny module so tests + components
 * can read auth-mode + persisted-token from one place. The actual
 * state machine lives in `contexts/AnonymousGroupAuthProvider.tsx`;
 * this module is the pure storage + env-var layer.
 *
 * Storage policy: ``sessionStorage`` (NOT ``localStorage``). Anonymous
 * sessions are intentionally short-lived — closing the tab discards
 * the token. Forks that want persistence across tabs should use a
 * different auth mode.
 */

/** Storage key the provider writes its token+uid+expires_at to. */
export const ANON_GROUP_TOKEN_STORAGE_KEY = "aitana:anon_group_session";

/**
 * Returns ``true`` when ``NEXT_PUBLIC_AUTH_MODE`` is set to the
 * anonymous-group-id value. False otherwise (default Firebase /
 * LOCAL_MODE branches keep working).
 *
 * Truthy values for the env var: the exact string
 * ``"anonymous_group_id"`` only — keeps the mode switch unambiguous.
 */
export function isAnonymousGroupAuthMode(): boolean {
  // G20: `||` not `??` — both `unset` and `set-to-""` mean "no auth mode declared".
  const raw = (process.env.NEXT_PUBLIC_AUTH_MODE || "").trim().toLowerCase();
  return raw === "anonymous_group_id";
}

/** Persisted shape written to sessionStorage. */
export interface PersistedGroupSession {
  token: string;
  uid: string;
  /** Unix seconds at which the token expires. */
  expires_at: number;
}

/**
 * Read the persisted group session if present + still fresh.
 *
 * Returns ``null`` when:
 *   - SSR (no window / sessionStorage)
 *   - Nothing stored
 *   - Stored payload is malformed (treated as no session)
 *   - ``expires_at`` is in the past (stale token is purged + null returned)
 */
export function readStoredGroupSession(): PersistedGroupSession | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(ANON_GROUP_TOKEN_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PersistedGroupSession;
    if (
      typeof parsed.token !== "string" ||
      typeof parsed.uid !== "string" ||
      typeof parsed.expires_at !== "number"
    ) {
      window.sessionStorage.removeItem(ANON_GROUP_TOKEN_STORAGE_KEY);
      return null;
    }
    // Stale → drop + treat as no session.
    if (parsed.expires_at <= Date.now() / 1000) {
      window.sessionStorage.removeItem(ANON_GROUP_TOKEN_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    window.sessionStorage.removeItem(ANON_GROUP_TOKEN_STORAGE_KEY);
    return null;
  }
}

/** Persist a freshly-minted session. */
export function writeStoredGroupSession(session: PersistedGroupSession): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(
    ANON_GROUP_TOKEN_STORAGE_KEY,
    JSON.stringify(session),
  );
}

/** Clear the persisted session (used by clearStoredToken + markExpired). */
export function clearStoredGroupSession(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(ANON_GROUP_TOKEN_STORAGE_KEY);
}

/**
 * Normalise a user-typed group code to the canonical wire shape.
 *
 * The backend mints codes in the form ``XXXX-XXXX`` using uppercase
 * letters + digits, no ambiguous chars (``0/O/1/I``). Users typing on
 * a phone or copying from a chat often send lowercase or extra
 * whitespace. We strip + uppercase here so the backend match is
 * deterministic.
 */
export function normalizeGroupCode(input: string): string {
  return input.trim().toUpperCase();
}
