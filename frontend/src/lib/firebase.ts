import { type FirebaseApp, getApps, initializeApp } from "firebase/app";
import {
  type Auth,
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  onIdTokenChanged,
  signInWithPopup,
  signInWithRedirect,
  signOut as fbSignOut,
  type User,
} from "firebase/auth";
import { type Firestore, getFirestore } from "firebase/firestore";
import {
  isAnonymousGroupAuthMode,
  readStoredGroupSession,
} from "@/lib/anonymousGroupAuth";
import { isLocalMode, LOCAL_MODE_STUB_TOKEN } from "@/lib/localMode";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

let appInstance: FirebaseApp | null = null;
let authInstance: Auth | null = null;

function isConfigured(): boolean {
  return Boolean(firebaseConfig.apiKey && firebaseConfig.projectId);
}

export function getFirebaseApp(): FirebaseApp | null {
  // LOCAL_MODE: no Firebase init at all — LocalAuthProvider supplies a
  // deterministic identity. Returning null keeps existing `if (!app)`
  // branches working without further changes.
  if (isLocalMode()) return null;
  if (!isConfigured()) return null;
  if (appInstance) return appInstance;
  appInstance = getApps()[0] ?? initializeApp(firebaseConfig);
  return appInstance;
}

export function getFirebaseAuth(): Auth | null {
  if (authInstance) return authInstance;
  const app = getFirebaseApp();
  if (!app) return null;
  authInstance = getAuth(app);
  return authInstance;
}

export function subscribeToAuthState(
  callback: (user: User | null) => void,
): () => void {
  const auth = getFirebaseAuth();
  if (!auth) {
    // Not configured (e.g. build time or missing env). Report signed-out.
    callback(null);
    return () => {};
  }
  return onAuthStateChanged(auth, callback);
}

/**
 * Which identity a request should be made as.
 *
 * The platform runs ONE auth mode at a time, so `"active"` is almost always
 * right and is the default. The other values exist for forks that run two
 * audiences CONCURRENTLY — e.g. staff on Firebase and end-users on group
 * codes, against a backend that ACLs some endpoints for both.
 *
 * That fork shape is where this footgun lives (AIPLA #33/#34): with only a
 * mode-dispatching `getIdToken()` and no way to say which audience a call is
 * for, a module author picks one helper for the whole file and silently
 * breaks the other role — a 401 on an endpoint the caller was entitled to.
 * It shipped there four times. Naming the audience at the call site is the
 * seam that makes the second role expressible instead of a fork in the code.
 */
export type AuthAudience = "active" | "firebase" | "group" | "either";

/**
 * Resolve a token for a specific audience.
 *
 * - `active`   — whatever mode this deployment is in (current behaviour).
 * - `firebase` — an individually-identified user; null if not signed in.
 * - `group`    — a shared group-code session; null if there isn't one.
 * - `either`   — prefer a Firebase identity, fall back to a group session.
 *   Use for endpoints the backend ACLs for both audiences.
 */
export async function getIdTokenFor(
  audience: AuthAudience = "active",
  forceRefresh = false,
): Promise<string | null> {
  if (audience === "active") return getIdToken(forceRefresh);

  const groupToken = () => readStoredGroupSession()?.token ?? null;
  const firebaseToken = async () => {
    if (isLocalMode()) return LOCAL_MODE_STUB_TOKEN;
    const auth = getFirebaseAuth();
    if (!auth?.currentUser) return null;
    return auth.currentUser.getIdToken(forceRefresh);
  };

  if (audience === "group") return groupToken();
  if (audience === "firebase") return firebaseToken();
  return (await firebaseToken()) ?? groupToken();
}

export async function getIdToken(forceRefresh = false): Promise<string | null> {
  // Anonymous group-ID mode (sprint 2.11): token lives in sessionStorage,
  // written by AnonymousGroupAuthProvider. Checked BEFORE LOCAL_MODE
  // because the group-auth env var explicitly overrides LOCAL_MODE
  // when forks want to demo without standing up Firebase.
  if (isAnonymousGroupAuthMode()) {
    const session = readStoredGroupSession();
    return session?.token ?? null;
  }
  // LOCAL_MODE: every request sends the well-known stub token so the
  // backend's auth/local_mode_stub.py grants it. fetchWithAuth wires this
  // into the Authorization header on every /api/proxy/* request.
  if (isLocalMode()) return LOCAL_MODE_STUB_TOKEN;
  const auth = getFirebaseAuth();
  if (!auth?.currentUser) return null;
  // forceRefresh re-mints the token from Firebase, which is the ONLY way to
  // pick up custom claims (e.g. a just-granted `tenant-admin:{domain}`) before
  // the ~1h natural rotation. Used by the admin console's "Re-check my access"
  // so a newly-granted admin isn't left staring at a surface that silently
  // refuses them for an hour. Stub/anonymous modes above ignore it — they have
  // no claims to refresh.
  return auth.currentUser.getIdToken(forceRefresh);
}

/**
 * Subscribe to ID-token changes — fires immediately with the current
 * token, then again whenever Firebase rotates the token (every ~55min
 * silently, plus on sign-in, sign-out, and tab focus when the prior
 * token has expired).
 *
 * G40 (template-auth-token-refresh.md): the AGUIProvider used to call
 * ``getIdToken()`` once at mount and bake the result into the
 * HttpAgent's Authorization header. After ~1 hour the Firebase ID token
 * expires server-side; the next ``runAgent()`` call gets a 401 and the
 * UI looks like the agent broke. The user's only workaround was a page
 * refresh. This helper plus the in-place header mutation in
 * AGUIProvider fix the trap once for every fork — long-running demos,
 * workshops, multi-hour sessions all keep working.
 *
 * Per-mode behaviour:
 *   - **LOCAL_MODE** — fires once synchronously with the stub token.
 *     The stub never expires, so there's no refresh channel to wire.
 *   - **Anonymous-group** — fires once synchronously with the stored
 *     group token. Group-token rotation lives in
 *     ``AnonymousGroupAuthProvider`` (it writes a new token to
 *     sessionStorage); subscribers don't get notified here today.
 *     Forks needing tighter refresh can wrap that provider.
 *   - **Firebase Auth** — wires ``onIdTokenChanged`` so silent ~hourly
 *     rotations + sign-in/out events all surface to the subscriber.
 *
 * Returns an unsubscribe function (no-op for the LOCAL_MODE /
 * anonymous-group paths since there's no listener to clean up).
 */
export function subscribeToIdToken(
  callback: (token: string | null) => void,
): () => void {
  // Anonymous group-ID mode: synchronous one-shot, no refresh wire-up.
  // The provider writes new tokens to sessionStorage; consumers that
  // need tighter refresh should subscribe to a separate channel there.
  if (isAnonymousGroupAuthMode()) {
    const session = readStoredGroupSession();
    callback(session?.token ?? null);
    return () => {};
  }
  // LOCAL_MODE: stub token never expires; deliver once and stop.
  if (isLocalMode()) {
    callback(LOCAL_MODE_STUB_TOKEN);
    return () => {};
  }
  const auth = getFirebaseAuth();
  if (!auth) {
    callback(null);
    return () => {};
  }
  // Firebase: onIdTokenChanged fires on every token change including
  // sign-in, sign-out, and silent ~hourly auto-refresh. We resolve the
  // new token via getIdToken() rather than user.getIdToken() so the
  // user-null sign-out path delivers a clean null instead of throwing.
  return onIdTokenChanged(auth, () => {
    void getIdToken().then((t) => callback(t));
  });
}

/**
 * Sign in with Google. Prefers a popup; on Safari (which blocks third-party
 * storage the popup flow relies on unless the user clicks very recently), the
 * caller can fall back to `signInWithGoogleRedirect`. We do NOT try to detect
 * Safari automatically — popup failure is caught by the caller and re-tried
 * via redirect on that code path.
 */
export async function signInWithGoogle(): Promise<void> {
  const auth = getFirebaseAuth();
  if (!auth) throw new Error("firebase not configured");
  const provider = new GoogleAuthProvider();
  await signInWithPopup(auth, provider);
}

export async function signInWithGoogleRedirect(): Promise<void> {
  const auth = getFirebaseAuth();
  if (!auth) throw new Error("firebase not configured");
  const provider = new GoogleAuthProvider();
  await signInWithRedirect(auth, provider);
}

export async function signOut(): Promise<void> {
  const auth = getFirebaseAuth();
  if (!auth) return;
  await fbSignOut(auth);
}

export function getFirestoreDb(): Firestore | null {
  const app = getFirebaseApp();
  if (!app) return null;
  return getFirestore(app);
}

export function firestoreTimestampToIso(value: unknown): string | null {
  if (!value) return null;
  if (typeof value === "string") return value;
  if (typeof value === "object" && value !== null && "toDate" in value) {
    const d = (value as { toDate: () => Date }).toDate?.();
    return d instanceof Date ? d.toISOString() : null;
  }
  return null;
}

export type { User };
