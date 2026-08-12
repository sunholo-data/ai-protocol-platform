/**
 * `fetchWithAuth` — thin wrapper around `fetch` that attaches the current
 * Firebase ID token as `Authorization: Bearer <jwt>` so the backend's
 * `Depends(get_current_user)` can verify it.
 *
 * Callers should pass paths relative to the Next app (e.g. `/api/proxy/api/
 * skills`), not absolute backend URLs — the Next catch-all at
 * `app/api/proxy/[...path]/route.ts` then forwards to the sidecar with the
 * header preserved.
 *
 * If no user is signed in, the request is still sent (without the header);
 * the backend decides whether the route is public. This keeps callers simple
 * — they don't need to branch on "am I signed in yet?" before every request.
 *
 * Domain allowlist (v6.18.0 Gap B): when the deployment restricts who may
 * authenticate (`AUTH_REQUIRE_KNOWN_DOMAIN`), the backend 403s a rejected
 * caller with `{detail:{code:"DOMAIN_NOT_PERMITTED"}}` on EVERY authenticated
 * call. Firebase sign-in itself still succeeds (Firebase doesn't know the
 * allowlist), so without a signal the user lands "signed in" on a broken app.
 * We detect that code centrally here and dispatch a window event so a top-level
 * gate can render a clear "access restricted" screen (NEVER-SILENT #8) — every
 * one of the ~76 `fetchWithAuth` callers inherits this without changes.
 */

import { getIdToken, getIdTokenFor, type AuthAudience } from "@/lib/firebase";

/** Window event fired when the backend rejects the caller's email domain. */
export const DOMAIN_NOT_PERMITTED_EVENT = "aitana:domain-not-permitted";

async function maybeSignalDomainRejection(res: Response): Promise<void> {
  // Only a 403 can carry the rejection; parse a CLONE so the caller's body
  // stream stays intact. A non-JSON / differently-shaped 403 is ignored.
  try {
    const body = (await res.json()) as { detail?: { code?: string; message?: string } };
    const detail = body?.detail;
    if (detail && typeof detail === "object" && detail.code === "DOMAIN_NOT_PERMITTED") {
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent(DOMAIN_NOT_PERMITTED_EVENT, { detail: { message: detail.message } }),
        );
      }
    }
  } catch {
    // Not the structured domain-rejection payload — leave it to the caller.
  }
}

/** Options that are ours, not `fetch`'s. */
export interface AuthFetchOptions {
  /**
   * Which identity to send as. Defaults to `"active"` — the deployment's
   * current auth mode — which is correct whenever only one mode is in play.
   *
   * Pass an explicit audience for an endpoint the backend ACLs for more than
   * one role. See `AuthAudience` for why this seam exists.
   */
  audience?: AuthAudience;
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
  { audience = "active" }: AuthFetchOptions = {},
): Promise<Response> {
  // Default path stays on `getIdToken` rather than routing through
  // `getIdTokenFor("active")`. Semantically identical, but it keeps the
  // existing call graph — ~76 callers and their tests mock `getIdToken`, and
  // silently moving off it would break every one of those mocks while the
  // behaviour looked unchanged.
  const token = audience === "active" ? await getIdToken() : await getIdTokenFor(audience);
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(input, { ...init, headers, cache: "no-store" });
  if (res.status === 403) {
    // Fire-and-forget: never block or alter the caller's response.
    void maybeSignalDomainRejection(res.clone());
  }
  return res;
}
