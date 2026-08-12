/**
 * AnonymousGroupAuthProvider state machine tests (sprint 2.11, M3).
 *
 * The provider has four states: idle | joining | joined | expired.
 * Storage is sessionStorage (NOT localStorage) — anonymous sessions
 * do not survive a tab close.
 *
 * The state machine MUST transition cleanly:
 *   idle    → joining   on join()
 *   joining → joined    on success
 *   joining → idle      on failure (so the user can retry)
 *   joined  → expired   on a 401 from any downstream fetch
 *   expired → idle      after clearStoredToken()
 */
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock fetch so the provider's join() call is deterministic.
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

import {
  AnonymousGroupAuthProvider,
  useAnonymousGroupAuth,
} from "@/contexts/AnonymousGroupAuthProvider";
import { ANON_GROUP_TOKEN_STORAGE_KEY } from "@/lib/anonymousGroupAuth";

function wrap({ children }: { children: ReactNode }) {
  return <AnonymousGroupAuthProvider>{children}</AnonymousGroupAuthProvider>;
}

const HAPPY_RESPONSE = {
  token: "eyJhbGc.test.token",
  uid: "anon-PHYS7K2N-deadbeef",
  expires_at: Date.now() / 1000 + 28800,
};

function mockFetchSuccess(body = HAPPY_RESPONSE) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

function mockFetchFailure(status: number, body: unknown = { detail: "x" }) {
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: new Headers(status === 429 ? { "retry-after": "12" } : {}),
  } as Response);
}

beforeEach(() => {
  fetchMock.mockReset();
  sessionStorage.clear();
});

describe("AnonymousGroupAuthProvider — state machine", () => {
  it("starts in idle state with no user", () => {
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    expect(result.current.status).toBe("idle");
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it("transitions idle → joining → joined on successful join", async () => {
    mockFetchSuccess();
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await result.current.join("PHYS-7K2N");
    });
    expect(result.current.status).toBe("joined");
    expect(result.current.user?.uid).toBe("anon-PHYS7K2N-deadbeef");
    expect(result.current.token).toBe("eyJhbGc.test.token");
  });

  it("transitions joining → idle on failed join (so user can retry)", async () => {
    mockFetchFailure(401, { detail: "group not found or no longer active" });
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await expect(result.current.join("NOPE-XXXX")).rejects.toThrow();
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.user).toBeNull();
    expect(result.current.error?.kind).toBe("unknown_or_revoked");
  });

  it("surfaces rate-limit retry-after on 429", async () => {
    mockFetchFailure(429, { detail: "rate limit exceeded; retry after 12s" });
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await expect(result.current.join("PHYS-7K2N")).rejects.toThrow();
    });
    const err = result.current.error;
    expect(err?.kind).toBe("rate_limited");
    if (err?.kind === "rate_limited") {
      expect(err.retryAfterSeconds).toBe(12);
    }
  });

  it("surfaces at-capacity error on 503", async () => {
    mockFetchFailure(503, { detail: "cap exceeded" });
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await expect(result.current.join("PHYS-7K2N")).rejects.toThrow();
    });
    expect(result.current.error?.kind).toBe("at_capacity");
  });

  it("transitions joined → expired on markExpired()", async () => {
    mockFetchSuccess();
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await result.current.join("PHYS-7K2N");
    });
    expect(result.current.status).toBe("joined");
    act(() => {
      result.current.markExpired();
    });
    expect(result.current.status).toBe("expired");
  });

  it("clearStoredToken returns to idle and drops sessionStorage", async () => {
    mockFetchSuccess();
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await result.current.join("PHYS-7K2N");
    });
    expect(sessionStorage.getItem(ANON_GROUP_TOKEN_STORAGE_KEY)).not.toBeNull();

    act(() => {
      result.current.clearStoredToken();
    });
    expect(result.current.status).toBe("idle");
    expect(sessionStorage.getItem(ANON_GROUP_TOKEN_STORAGE_KEY)).toBeNull();
    expect(result.current.user).toBeNull();
  });
});

describe("AnonymousGroupAuthProvider — sessionStorage", () => {
  it("persists token to sessionStorage on successful join", async () => {
    mockFetchSuccess();
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await result.current.join("PHYS-7K2N");
    });
    const stored = sessionStorage.getItem(ANON_GROUP_TOKEN_STORAGE_KEY);
    expect(stored).toBeTruthy();
    const parsed = JSON.parse(stored!);
    expect(parsed.token).toBe("eyJhbGc.test.token");
    expect(parsed.uid).toBe("anon-PHYS7K2N-deadbeef");
  });

  it("re-hydrates from sessionStorage on mount", async () => {
    sessionStorage.setItem(
      ANON_GROUP_TOKEN_STORAGE_KEY,
      JSON.stringify({
        token: "stored-token",
        uid: "anon-X-y",
        expires_at: Date.now() / 1000 + 3600,
      }),
    );
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await waitFor(() => {
      expect(result.current.status).toBe("joined");
    });
    expect(result.current.token).toBe("stored-token");
    expect(result.current.user?.uid).toBe("anon-X-y");
  });

  it("ignores stored token whose expires_at is in the past", async () => {
    sessionStorage.setItem(
      ANON_GROUP_TOKEN_STORAGE_KEY,
      JSON.stringify({
        token: "stale-token",
        uid: "anon-X-y",
        expires_at: Date.now() / 1000 - 60, // 1 minute ago
      }),
    );
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await waitFor(() => {
      // Should NOT hydrate; status stays idle.
      expect(result.current.status).toBe("idle");
    });
    // Stale token cleared from storage on detection.
    expect(sessionStorage.getItem(ANON_GROUP_TOKEN_STORAGE_KEY)).toBeNull();
  });
});

describe("AnonymousGroupAuthProvider — code normalization", () => {
  it("uppercases and trims the group code before POST", async () => {
    mockFetchSuccess();
    const { result } = renderHook(() => useAnonymousGroupAuth(), { wrapper: wrap });
    await act(async () => {
      await result.current.join("  phys-7k2n  ");
    });
    // Last fetch call's body has the normalized code.
    const lastCall = fetchMock.mock.calls.at(-1);
    const body = JSON.parse(lastCall![1].body);
    expect(body.group_id).toBe("PHYS-7K2N");
  });
});

describe("useAnonymousGroupAuth hook misuse", () => {
  it("throws when used outside the provider", () => {
    // Hook out of provider should throw a recognisable error.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAnonymousGroupAuth())).toThrow(
      /AnonymousGroupAuthProvider/,
    );
    spy.mockRestore();
  });
});
