import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UserMenu } from "../UserMenu";
import { fetchWithAuth } from "@/lib/apiClient";

// Mutable auth stub — the mocked useAuth reads it at call time, so tests can
// flip user state between cases.
const auth = {
  user: null as { displayName?: string; email?: string; photoURL?: string | null } | null,
  loading: false,
  signIn: vi.fn(),
  signOut: vi.fn(),
  getIdToken: vi.fn(),
  signInWithRedirect: vi.fn(),
};

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

describe("UserMenu", () => {
  beforeEach(() => {
    // Default: the admin probe denies (non-admin). Individual tests override.
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: false, status: 403 } as Response);
  });

  it("shows Sign in when logged out and calls signIn on click", () => {
    auth.user = null;
    render(<UserMenu />);
    const btn = screen.getByText("Sign in");
    fireEvent.click(btn);
    expect(auth.signIn).toHaveBeenCalledOnce();
  });

  it("shows the avatar menu with name/email and Sign out when logged in", () => {
    auth.user = { displayName: "Mark", email: "owner@yourcompany.com", photoURL: null };
    render(<UserMenu />);
    // Menu is closed initially — open it.
    fireEvent.click(screen.getByLabelText("Account menu"));
    expect(screen.getByText("Mark")).toBeTruthy();
    expect(screen.getByText("owner@yourcompany.com")).toBeTruthy();
    fireEvent.click(screen.getByText("Sign out"));
    expect(auth.signOut).toHaveBeenCalledOnce();
  });

  it("falls back to an initial chip when there is no photoURL", () => {
    auth.user = { displayName: "Zara", email: "z@x.com", photoURL: null };
    render(<UserMenu />);
    // The trigger shows the uppercase initial.
    expect(screen.getByLabelText("Account menu").textContent).toBe("Z");
  });

  it("reveals an Admin link for a platform admin", async () => {
    auth.user = { displayName: "Mark", email: "owner@yourcompany.com", photoURL: null };
    vi.mocked(fetchWithAuth).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ scope: "platform", domains: [] }),
    } as unknown as Response);
    render(<UserMenu />);
    fireEvent.click(screen.getByLabelText("Account menu"));
    const adminLink = await screen.findByText("Admin");
    expect(adminLink.getAttribute("href")).toBe("/admin");
    // v6.16.0: asks the ROLE endpoint, not a platform-only data endpoint.
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/admin/whoami");
  });

  it("reveals an Admin link for a TENANT admin (the v6.16.0 fix)", async () => {
    // The whole point of the whoami probe: this user used to see nothing,
    // because the old probe hit a platform-only endpoint and got a 403.
    auth.user = { displayName: "Ops", email: "ops@acme-energy.example", photoURL: null };
    vi.mocked(fetchWithAuth).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ scope: "tenant", domains: ["acme-energy.example"] }),
    } as unknown as Response);
    render(<UserMenu />);
    fireEvent.click(screen.getByLabelText("Account menu"));
    const adminLink = await screen.findByText("Admin");
    expect(adminLink.getAttribute("href")).toBe("/admin");
  });

  it("hides the Admin link for a non-admin (scope 'none')", async () => {
    auth.user = { displayName: "Zed", email: "zed@x.com", photoURL: null };
    vi.mocked(fetchWithAuth).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ scope: "none", domains: [] }),
    } as unknown as Response);
    render(<UserMenu />);
    fireEvent.click(screen.getByLabelText("Account menu"));
    // Let the probe resolve, then assert the Admin item never appears.
    await screen.findByText("Sign out");
    expect(screen.queryByText("Admin")).toBeNull();
  });
});
