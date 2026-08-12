import { describe, expect, it, vi } from "vitest";
import { render, renderHook, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";

vi.mock("@/lib/firebase", () => ({
  subscribeToAuthState: (cb: (u: null) => void) => {
    // Simulate async auth resolution with signed-out user.
    queueMicrotask(() => cb(null));
    return () => {};
  },
  getIdToken: async () => null,
  signInWithGoogle: async () => {},
  signInWithGoogleRedirect: async () => {},
  signOut: async () => {},
}));

describe("useAuth", () => {
  it("throws when used outside an AuthProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAuth())).toThrow(
      /must be used within an AuthProvider/,
    );
    spy.mockRestore();
  });

  it("starts in loading state and resolves to signed-out", async () => {
    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    expect(result.current.loading).toBe(true);
    expect(result.current.user).toBeNull();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toBeNull();
  });

  it("renders children", () => {
    const { getByText } = render(
      <AuthProvider>
        <div>hello</div>
      </AuthProvider>,
    );
    expect(getByText("hello")).toBeTruthy();
  });
});
