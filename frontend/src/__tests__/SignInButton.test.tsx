import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";

// Mutable mock state so each test can install its own user + fakes.
const firebaseMock = {
  user: null as { email: string } | null,
  signInWithGoogle: vi.fn(async () => {}),
  signInWithGoogleRedirect: vi.fn(async () => {}),
  signOut: vi.fn(async () => {}),
};

vi.mock("@/lib/firebase", () => ({
  subscribeToAuthState: (cb: (u: { email: string } | null) => void) => {
    queueMicrotask(() => cb(firebaseMock.user));
    return () => {};
  },
  getIdToken: async () => (firebaseMock.user ? "fake-id-token" : null),
  signInWithGoogle: () => firebaseMock.signInWithGoogle(),
  signInWithGoogleRedirect: () => firebaseMock.signInWithGoogleRedirect(),
  signOut: () => firebaseMock.signOut(),
}));

// Imports must come AFTER vi.mock to pick up the mocked module.
// eslint-disable-next-line import/first
import { AuthProvider } from "@/contexts/AuthContext";
// eslint-disable-next-line import/first
import { SignInButton } from "@/components/SignInButton";

function renderButton() {
  return render(
    <AuthProvider>
      <SignInButton />
    </AuthProvider>,
  );
}

describe("SignInButton", () => {
  beforeEach(() => {
    firebaseMock.user = null;
    firebaseMock.signInWithGoogle.mockReset();
    firebaseMock.signInWithGoogle.mockImplementation(async () => {});
    firebaseMock.signInWithGoogleRedirect.mockReset();
    firebaseMock.signInWithGoogleRedirect.mockImplementation(async () => {});
    firebaseMock.signOut.mockReset();
    firebaseMock.signOut.mockImplementation(async () => {});
  });

  it("shows the Sign-In button when signed out", async () => {
    const { getByTestId, queryByTestId } = renderButton();
    await waitFor(() => {
      expect(queryByTestId("sign-in-loading")).toBeNull();
    });
    expect(getByTestId("sign-in-button").textContent).toMatch(/Sign in/i);
  });

  it("calls signInWithGoogle on click", async () => {
    const { getByTestId, queryByTestId } = renderButton();
    await waitFor(() => {
      expect(queryByTestId("sign-in-loading")).toBeNull();
    });
    fireEvent.click(getByTestId("sign-in-button"));
    await waitFor(() => {
      expect(firebaseMock.signInWithGoogle).toHaveBeenCalledTimes(1);
    });
    expect(firebaseMock.signInWithGoogleRedirect).not.toHaveBeenCalled();
  });

  it("falls back to redirect when popup sign-in rejects", async () => {
    firebaseMock.signInWithGoogle.mockRejectedValueOnce(new Error("popup blocked"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { getByTestId, queryByTestId } = renderButton();
    await waitFor(() => {
      expect(queryByTestId("sign-in-loading")).toBeNull();
    });
    fireEvent.click(getByTestId("sign-in-button"));
    await waitFor(() => {
      expect(firebaseMock.signInWithGoogleRedirect).toHaveBeenCalledTimes(1);
    });
    warnSpy.mockRestore();
  });

  it("shows signed-in chrome with email + Sign-Out when a user is present", async () => {
    firebaseMock.user = { email: "owner@yourcompany.com" };
    const { getByTestId, getByText, queryByTestId } = renderButton();
    await waitFor(() => {
      expect(queryByTestId("sign-in-loading")).toBeNull();
    });
    expect(getByTestId("signed-in")).toBeTruthy();
    expect(getByText("owner@yourcompany.com")).toBeTruthy();
  });

  it("calls signOut when Sign-Out is clicked", async () => {
    firebaseMock.user = { email: "owner@yourcompany.com" };
    const { getByText, queryByTestId } = renderButton();
    await waitFor(() => {
      expect(queryByTestId("sign-in-loading")).toBeNull();
    });
    fireEvent.click(getByText("Sign out"));
    await waitFor(() => {
      expect(firebaseMock.signOut).toHaveBeenCalledTimes(1);
    });
  });
});
