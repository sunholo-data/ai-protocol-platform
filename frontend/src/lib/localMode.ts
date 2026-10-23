/**
 * LOCAL_MODE detection on the frontend.
 *
 * Mirrors `backend/config/local_mode.py:is_local_mode()`. Reads
 * `NEXT_PUBLIC_LOCAL_MODE` at build/runtime (Next.js bakes
 * NEXT_PUBLIC_* into the client bundle). Truthy values: `1`, `true`,
 * `yes`, `on` (case-insensitive). Everything else is false.
 *
 * Why a tiny helper instead of inline checks: every component that
 * conditionally renders LOCAL_MODE UI (banner, dev-only links) reads
 * this — one helper means one place to mock in tests.
 */

export function isLocalMode(): boolean {
  // G20: `||` not `??` — both `unset` and `set-to-""` mean "not in local mode".
  const raw = (process.env.NEXT_PUBLIC_LOCAL_MODE || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

/**
 * Whether to show the LOCAL_MODE banner. On by default whenever LOCAL_MODE is
 * — it is the workshop's trust signal and its "graduate to GCP" funnel — but a
 * deployment that runs LOCAL_MODE on purpose (a private, single-user host) can
 * set `NEXT_PUBLIC_LOCAL_MODE_BANNER=0` to drop it.
 */
export function isLocalModeBannerEnabled(): boolean {
  if (!isLocalMode()) return false;
  const raw = (process.env.NEXT_PUBLIC_LOCAL_MODE_BANNER || "").trim().toLowerCase();
  return !(raw === "0" || raw === "false" || raw === "no" || raw === "off");
}

/** Well-known stub token the backend's LOCAL_MODE auth dep recognises. */
export const LOCAL_MODE_STUB_TOKEN = "local-mode-stub-token";

/** Workshop-user identity returned by `LocalAuthProvider` and matched
 *  by the backend's `auth/local_mode_stub.py`. */
export const LOCAL_MODE_WORKSHOP_USER = {
  uid: "workshop-user",
  email: "workshop@local",
  displayName: "Workshop Attendee",
  photoURL: null,
} as const;
