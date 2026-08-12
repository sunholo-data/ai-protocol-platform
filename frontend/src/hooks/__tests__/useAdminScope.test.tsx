// useAdminScope — the admin role probe (v6.16.0 M6).
//
// The property worth protecting: "you are not an admin" and "we couldn't find
// out" must stay DIFFERENT states. Collapsing them is what the old
// probe-a-data-endpoint approach did, and it produced two bugs at once — a
// tenant admin told they had no access, and a backend outage silently
// indistinguishable from a permissions denial.

import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAdminScope } from "../useAdminScope";

const auth: { user: unknown; loading: boolean } = { user: null, loading: false };

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => auth,
}));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";

function mockWhoAmI(body: { scope: string; domains?: string[] }) {
  vi.mocked(fetchWithAuth).mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response);
}

describe("useAdminScope", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { email: "u@x.com" };
    auth.loading = false;
  });

  it("resolves platform scope", async () => {
    mockWhoAmI({ scope: "platform", domains: [] });
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("platform"));
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isPlatform).toBe(true);
  });

  it("resolves tenant scope with its domains", async () => {
    mockWhoAmI({ scope: "tenant", domains: ["a.com"] });
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("tenant"));
    expect(result.current.isAdmin).toBe(true);
    // A tenant admin is an admin, but NOT a platform admin — platform-only
    // surfaces gate on this distinction.
    expect(result.current.isPlatform).toBe(false);
    expect(result.current.domains).toEqual(["a.com"]);
  });

  it("resolves 'none' for a signed-in non-admin", async () => {
    mockWhoAmI({ scope: "none", domains: [] });
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("none"));
    expect(result.current.isAdmin).toBe(false);
  });

  it("reports a network failure as 'error', NOT as 'not an admin'", async () => {
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.state).not.toBe("none");
  });

  it("reports a non-ok response as 'error' (whoami answers 200 for everyone)", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: false, status: 500 } as Response);
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("error"));
  });

  it("treats an unrecognised scope value as 'none' rather than trusting it", async () => {
    mockWhoAmI({ scope: "superuser", domains: [] });
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("none"));
    expect(result.current.isAdmin).toBe(false);
  });

  it("does not probe until enabled (lazy menu open)", async () => {
    mockWhoAmI({ scope: "platform" });
    const { rerender } = renderHook(({ on }) => useAdminScope(on), {
      initialProps: { on: false },
    });
    expect(fetchWithAuth).not.toHaveBeenCalled();
    rerender({ on: true });
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/admin/whoami"));
  });

  it("does not probe when signed out", () => {
    auth.user = null;
    renderHook(() => useAdminScope());
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("probes at most once", async () => {
    mockWhoAmI({ scope: "platform" });
    const { result, rerender } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("platform"));
    rerender();
    rerender();
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("tolerates a malformed domains field", async () => {
    mockWhoAmI({ scope: "tenant", domains: undefined });
    const { result } = renderHook(() => useAdminScope());
    await waitFor(() => expect(result.current.state).toBe("tenant"));
    expect(result.current.domains).toEqual([]);
  });
});
