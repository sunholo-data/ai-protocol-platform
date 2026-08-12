/**
 * Audience-explicit auth (v6.19.0, AIPLA #33/#34).
 *
 * This platform runs ONE auth mode at a time, so the reported symptom (a
 * dual-audience endpoint 401ing for one of two concurrent roles) cannot
 * reproduce here. What CAN reproduce is the cause: with no way to name the
 * audience at a call site, a fork that adds a second concurrent role has
 * nowhere to express it and picks one helper per module instead. That shipped
 * four times in the fork that reported it.
 *
 * So these tests pin the seam, not the symptom: `"active"` must stay exactly
 * what it is today (no behaviour change for this repo), and each explicit
 * audience must resolve the identity it names.
 *
 * Mocking follows the pattern established by `subscribeToIdToken.test.ts` —
 * mock at the firebase SDK boundary, because `getIdTokenFor` calls
 * `getFirebaseAuth()` internally and a spy on the export would not intercept
 * a module's call to itself.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

// Must run before the firebase module is imported: `firebaseConfig` is
// captured at module-load time, so `isConfigured()` has to be truthy here for
// the `getAuth` path to be reachable at all.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "test-api-key";
  process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "test-project";
});

const state = vi.hoisted(() => ({
  localMode: false,
  anonGroupMode: false,
  storedGroupToken: null as string | null,
  authConfigured: false,
  // Stable identity: firebase.ts caches the auth instance at module scope
  // after the first getAuth(app), so mutate currentUser rather than reassign.
  fakeAuth: { currentUser: null as { getIdToken: () => Promise<string> } | null },
}));

vi.mock("@/lib/localMode", async () => {
  const actual = await vi.importActual<typeof import("@/lib/localMode")>("@/lib/localMode");
  return { ...actual, isLocalMode: () => state.localMode };
});

vi.mock("@/lib/anonymousGroupAuth", () => ({
  isAnonymousGroupAuthMode: () => state.anonGroupMode,
  readStoredGroupSession: () => (state.storedGroupToken ? { token: state.storedGroupToken } : null),
}));

vi.mock("firebase/auth", async () => {
  const actual = await vi.importActual<typeof import("firebase/auth")>("firebase/auth");
  return { ...actual, getAuth: () => (state.authConfigured ? state.fakeAuth : null) };
});

vi.mock("firebase/app", async () => {
  const actual = await vi.importActual<typeof import("firebase/app")>("firebase/app");
  return { ...actual, getApps: () => [{ name: "test-app" }], initializeApp: () => ({ name: "test-app" }) };
});

import { getIdTokenFor } from "@/lib/firebase";

function signedInAs(token: string) {
  state.authConfigured = true;
  state.fakeAuth.currentUser = { getIdToken: vi.fn(async () => token) };
}

describe("getIdTokenFor", () => {
  beforeEach(() => {
    state.localMode = false;
    state.anonGroupMode = false;
    state.storedGroupToken = null;
    state.authConfigured = false;
    state.fakeAuth.currentUser = null;
  });

  describe("active (the default) — must not change for this repo", () => {
    it("uses the group token when the deployment is in group mode", async () => {
      state.anonGroupMode = true;
      state.storedGroupToken = "group-tok";

      await expect(getIdTokenFor("active")).resolves.toBe("group-tok");
    });

    it("uses the stub token in LOCAL_MODE", async () => {
      state.localMode = true;

      await expect(getIdTokenFor("active")).resolves.toBe("local-mode-stub-token");
    });

    it("defaults to active when no audience is given", async () => {
      state.localMode = true;

      await expect(getIdTokenFor()).resolves.toBe("local-mode-stub-token");
    });

    it("uses the Firebase identity when that is the deployment's mode", async () => {
      signedInAs("fb-tok");

      await expect(getIdTokenFor("active")).resolves.toBe("fb-tok");
    });
  });

  describe("explicit audiences", () => {
    it("group resolves the group session even when NOT in group mode", async () => {
      // The fork case: a group session alongside a Firebase identity.
      state.anonGroupMode = false;
      state.storedGroupToken = "group-tok";

      await expect(getIdTokenFor("group")).resolves.toBe("group-tok");
    });

    it("group is null when there is no group session", async () => {
      await expect(getIdTokenFor("group")).resolves.toBeNull();
    });

    it("firebase ignores a group session and is null when signed out", async () => {
      state.anonGroupMode = true;
      state.storedGroupToken = "group-tok";

      await expect(getIdTokenFor("firebase")).resolves.toBeNull();
    });

    it("either prefers a Firebase identity over a group session", async () => {
      state.storedGroupToken = "group-tok";
      signedInAs("fb-tok");

      await expect(getIdTokenFor("either")).resolves.toBe("fb-tok");
    });

    it("either falls back to the group session when signed out", async () => {
      state.storedGroupToken = "group-tok";

      await expect(getIdTokenFor("either")).resolves.toBe("group-tok");
    });

    it("either is null when neither identity exists", async () => {
      await expect(getIdTokenFor("either")).resolves.toBeNull();
    });
  });
});
