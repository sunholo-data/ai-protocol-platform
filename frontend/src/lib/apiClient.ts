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
    // EMAIL_NOT_VERIFIED (unverified-email gate) is the same dead end — every authed
    // call 403s for this identity — so it reuses the gate, carrying its code.
    if (
      detail &&
      typeof detail === "object" &&
      (detail.code === "DOMAIN_NOT_PERMITTED" || detail.code === "EMAIL_NOT_VERIFIED")
    ) {
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent(DOMAIN_NOT_PERMITTED_EVENT, {
            detail: { message: detail.message, code: detail.code },
          }),
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
  /**
   * How long to wait for the response to START (headers), in ms. Once the
   * response begins the timer is cleared, so a long streamed body is never cut
   * off. `0` disables it. Defaults to {@link DEFAULT_RESPONSE_TIMEOUT_MS},
   * except for uploads (FormData/Blob bodies), which have no default: their
   * time-to-first-byte includes the upload itself.
   */
  timeoutMs?: number;
}

/** A backend that never answers must not leave "Loading…" up forever. */
export const DEFAULT_RESPONSE_TIMEOUT_MS = 60_000;

/** Thrown when the server does not start responding within `timeoutMs`. */
export class FetchTimeoutError extends Error {
  constructor() {
    super("The server took too long to respond. Try again.");
    this.name = "TimeoutError";
  }
}

function isUploadBody(body: RequestInit["body"]): boolean {
  return (
    (typeof FormData !== "undefined" && body instanceof FormData) ||
    (typeof Blob !== "undefined" && body instanceof Blob)
  );
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
  { audience = "active", timeoutMs }: AuthFetchOptions = {},
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
  const limit = timeoutMs ?? (isUploadBody(init.body) ? 0 : DEFAULT_RESPONSE_TIMEOUT_MS);
  const res = await fetchWithResponseTimeout(input, { ...init, headers, cache: "no-store" }, limit);
  if (res.status === 403) {
    // Fire-and-forget: never block or alter the caller's response.
    void maybeSignalDomainRejection(res.clone());
  }
  return res;
}

/**
 * `fetch` that rejects with {@link FetchTimeoutError} if the response has not
 * started within `limitMs`. The caller's own `signal` still aborts as before.
 */
async function fetchWithResponseTimeout(
  input: RequestInfo | URL,
  init: RequestInit,
  limitMs: number,
): Promise<Response> {
  if (!limitMs || limitMs <= 0) return fetch(input, init);
  const controller = new AbortController();
  const callerSignal = init.signal;
  const forwardAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort(callerSignal.reason);
    else callerSignal.addEventListener("abort", forwardAbort, { once: true });
  }
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, limitMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (err) {
    if (timedOut) throw new FetchTimeoutError();
    throw err;
  } finally {
    // The caller's abort listener stays: it still has to cancel a streaming body.
    clearTimeout(timer);
  }
}
