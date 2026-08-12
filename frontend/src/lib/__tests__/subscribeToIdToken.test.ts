// G40 (template-auth-token-refresh.md) — unit tests for subscribeToIdToken.
//
// Contract (per `lib/firebase.ts`):
//   - LOCAL_MODE: fires once synchronously with LOCAL_MODE_STUB_TOKEN;
//     no refresh wire-up (stub doesn't expire).
//   - Anonymous-group mode: fires once synchronously with the stored
//     group token from sessionStorage; no refresh wire-up.
//   - Firebase Auth (configured): wires onIdTokenChanged so silent
//     ~hourly rotations + sign-in/out events all surface to the caller.
//   - Firebase Auth (not configured): fires once with null, returns no-op.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { LOCAL_MODE_STUB_TOKEN } from "@/lib/localMode";

// Set Firebase env vars BEFORE the firebase module is imported below, so
// `isConfigured()` evaluates truthy and the `getFirebaseAuth()` code
// path actually reaches our mocked `getAuth`. `firebaseConfig` is
// captured at module-load time, so this MUST run before the import.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "test-api-key";
  process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "test-project";
});

// Shared mutable state for the mocks (hoisted so vi.mock factories can see it).
//
// NOTE: `fakeAuth` is a stable object identity across tests. firebase.ts
// caches the auth instance at module scope after the first
// `getAuth(app)` call, so reassigning `state.fakeAuth = {…}` between
// tests doesn't update the cache. Instead, we mutate
// `fakeAuth.currentUser` per test. `authConfigured` controls whether
// `getAuth()` should hand back the stable object (configured path) or
// null (unconfigured path).
const state = vi.hoisted(() => ({
  localMode: false,
  anonGroupMode: false,
  storedGroupToken: null as string | null,
  authConfigured: false,
  fakeAuth: {
    currentUser: null as { getIdToken: () => Promise<string> } | null,
  },
  // The unsubscribe function our fake onIdTokenChanged returns.
  unsubscribeFn: vi.fn(),
  // Spy that captures every (auth, cb) pair passed to onIdTokenChanged.
  onIdTokenChangedSpy: vi.fn(),
}));

vi.mock("@/lib/localMode", async () => {
  const actual = await vi.importActual<typeof import("@/lib/localMode")>("@/lib/localMode");
  return {
    ...actual,
    isLocalMode: () => state.localMode,
  };
});

vi.mock("@/lib/anonymousGroupAuth", () => ({
  isAnonymousGroupAuthMode: () => state.anonGroupMode,
  readStoredGroupSession: () =>
    state.storedGroupToken ? { token: state.storedGroupToken } : null,
}));

// Mock firebase/auth at the SDK boundary. getAuth() returns the test's
// stable fake-auth object iff authConfigured=true; onIdTokenChanged
// registers a callback we capture.
vi.mock("firebase/auth", async () => {
  const actual = await vi.importActual<typeof import("firebase/auth")>("firebase/auth");
  return {
    ...actual,
    getAuth: () => (state.authConfigured ? state.fakeAuth : null),
    onIdTokenChanged: (auth: unknown, cb: (user: unknown) => void) => {
      state.onIdTokenChangedSpy(auth, cb);
      return state.unsubscribeFn;
    },
  };
});

// firebase/app mocks — getApps()/initializeApp() just need to return SOMETHING
// non-null so getFirebaseApp() doesn't try to call the real Firebase init.
vi.mock("firebase/app", async () => {
  const actual = await vi.importActual<typeof import("firebase/app")>("firebase/app");
  return {
    ...actual,
    getApps: () => [{} as ReturnType<typeof actual.initializeApp>],
    initializeApp: () => ({}) as ReturnType<typeof actual.initializeApp>,
  };
});

import { subscribeToIdToken } from "@/lib/firebase";

beforeEach(() => {
  state.localMode = false;
  state.anonGroupMode = false;
  state.storedGroupToken = null;
  state.authConfigured = false;
  state.fakeAuth.currentUser = null;
  state.unsubscribeFn = vi.fn();
  state.onIdTokenChangedSpy = vi.fn();
});

describe("subscribeToIdToken", () => {
  it("LOCAL_MODE: fires once with the stub token and returns a no-op unsubscribe", () => {
    state.localMode = true;
    const cb = vi.fn();

    const unsubscribe = subscribeToIdToken(cb);

    expect(cb).toHaveBeenCalledOnce();
    expect(cb).toHaveBeenCalledWith(LOCAL_MODE_STUB_TOKEN);
    // No Firebase wire-up — onIdTokenChanged was never called.
    expect(state.onIdTokenChangedSpy).not.toHaveBeenCalled();

    // Unsubscribe is a no-op (stub never expires) but must be safe to call.
    expect(() => unsubscribe()).not.toThrow();
  });

  it("anonymous-group: fires once with the stored token; no Firebase listener", () => {
    state.anonGroupMode = true;
    state.storedGroupToken = "group-token-abc";
    const cb = vi.fn();

    const unsubscribe = subscribeToIdToken(cb);

    expect(cb).toHaveBeenCalledOnce();
    expect(cb).toHaveBeenCalledWith("group-token-abc");
    expect(state.onIdTokenChangedSpy).not.toHaveBeenCalled();
    expect(() => unsubscribe()).not.toThrow();
  });

  it("anonymous-group with no stored session: fires once with null", () => {
    state.anonGroupMode = true;
    state.storedGroupToken = null;
    const cb = vi.fn();

    subscribeToIdToken(cb);

    expect(cb).toHaveBeenCalledOnce();
    expect(cb).toHaveBeenCalledWith(null);
  });

  it("Firebase Auth not configured: fires once with null and returns no-op", () => {
    state.authConfigured = false;
    const cb = vi.fn();

    const unsubscribe = subscribeToIdToken(cb);

    expect(cb).toHaveBeenCalledOnce();
    expect(cb).toHaveBeenCalledWith(null);
    expect(state.onIdTokenChangedSpy).not.toHaveBeenCalled();
    expect(() => unsubscribe()).not.toThrow();
  });

  it("Firebase Auth configured: registers onIdTokenChanged and resolves token on each fire", async () => {
    state.authConfigured = true;
    // currentUser.getIdToken returns the current token (test rotates it).
    let currentToken = "first-firebase-token";
    state.fakeAuth.currentUser = {
      getIdToken: () => Promise.resolve(currentToken),
    };
    const cb = vi.fn();

    subscribeToIdToken(cb);

    // Wired up with our stable fake-auth instance.
    expect(state.onIdTokenChangedSpy).toHaveBeenCalledTimes(1);
    const [authArg, firebaseHandler] = state.onIdTokenChangedSpy.mock.calls[0] as [
      unknown,
      () => void,
    ];
    expect(authArg).toBe(state.fakeAuth);

    // Simulate Firebase firing its handler (mount + sign-in).
    firebaseHandler();
    await new Promise((r) => setTimeout(r, 0)); // flush the async getIdToken

    expect(cb).toHaveBeenCalledWith("first-firebase-token");

    // Simulate silent ~hourly token refresh: Firebase fires the handler
    // again, getIdToken() now returns the new token.
    currentToken = "rotated-token-after-1h";
    firebaseHandler();
    await new Promise((r) => setTimeout(r, 0));

    expect(cb).toHaveBeenCalledWith("rotated-token-after-1h");
    expect(cb.mock.calls.map((c) => c[0])).toEqual([
      "first-firebase-token",
      "rotated-token-after-1h",
    ]);
  });

  it("Firebase Auth: sign-out (currentUser=null) delivers null to callback", async () => {
    state.authConfigured = true;
    state.fakeAuth.currentUser = {
      getIdToken: () => Promise.resolve("initial-token"),
    };
    const cb = vi.fn();

    subscribeToIdToken(cb);
    const firebaseHandler = state.onIdTokenChangedSpy.mock.calls[0][1] as () => void;

    firebaseHandler();
    await new Promise((r) => setTimeout(r, 0));
    expect(cb).toHaveBeenLastCalledWith("initial-token");

    // Sign-out: auth.currentUser becomes null; getIdToken() returns null
    // because firebase.ts's getIdToken short-circuits on `auth?.currentUser`.
    state.fakeAuth.currentUser = null;
    firebaseHandler();
    await new Promise((r) => setTimeout(r, 0));
    expect(cb).toHaveBeenLastCalledWith(null);
  });

  it("Firebase Auth: returned unsubscribe is the one Firebase gave us", () => {
    state.authConfigured = true;
    state.fakeAuth.currentUser = { getIdToken: () => Promise.resolve("t") };
    const cb = vi.fn();

    const unsubscribe = subscribeToIdToken(cb);
    // subscribeToIdToken passes the Firebase-returned unsubscribe through unchanged.
    expect(unsubscribe).toBe(state.unsubscribeFn);
  });
});
