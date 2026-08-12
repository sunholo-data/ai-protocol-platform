/**
 * LOCAL_MODE branch of AuthProvider — exposes a deterministic stub identity
 * without any Firebase init. This test pins the contract that:
 *   - useAuth() in LOCAL_MODE returns a user immediately (no loading flicker)
 *   - getIdToken() returns the well-known stub token
 *   - signIn / signOut are silent no-ops
 */

import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/localMode", async () => {
  const actual = await vi.importActual<typeof import("@/lib/localMode")>(
    "@/lib/localMode",
  );
  return {
    ...actual,
    isLocalMode: vi.fn(() => false),
  };
});

import * as localMode from "@/lib/localMode";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

describe("AuthProvider — LOCAL_MODE branch", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
  });

  it("exposes the workshop user immediately, no loading flicker", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user).not.toBeNull();
    expect(result.current.user?.uid).toBe("workshop-user");
    expect(result.current.user?.email).toBe("workshop@local");
    expect(result.current.loading).toBe(false);
  });

  it("getIdToken returns the well-known stub token", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    const token = await result.current.getIdToken();
    expect(token).toBe("local-mode-stub-token");
  });

  it("signIn / signInWithRedirect / signOut are silent no-ops", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await expect(result.current.signIn()).resolves.toBeUndefined();
    await expect(result.current.signInWithRedirect()).resolves.toBeUndefined();
    await expect(result.current.signOut()).resolves.toBeUndefined();
  });

  it("falls back to FirebaseAuthProvider when LOCAL_MODE is off", async () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(false);
    const { result } = renderHook(() => useAuth(), { wrapper });
    // Firebase isn't configured in tests, so subscribeToAuthState reports
    // null and loading flips to false on first tick.
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.user).toBeNull();
  });
});
