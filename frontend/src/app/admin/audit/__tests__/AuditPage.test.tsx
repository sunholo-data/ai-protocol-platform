// Audit trail view (v6.16.0 Phase 4).
//
// The subtle property here is the EMPTY state. "Nothing has happened" and
// "nothing here concerns your tenant" are different facts, and an empty table
// silently asserts the first. The backend returns a true pre-filter `scanned`
// count precisely so this page can tell them apart.

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuditPage from "../page";

const auth: { user: unknown; loading: boolean } = { user: { email: "a@x.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));

const scope = { state: "platform", domains: [] as string[], isAdmin: true, isPlatform: true };
vi.mock("@/hooks/useAdminScope", () => ({ useAdminScope: () => scope }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in</div> }));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";

function mockAudit(body: { entries: unknown[]; scanned: number; scope: string }) {
  vi.mocked(fetchWithAuth).mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response);
}

const ENTRY = {
  id: "1",
  ts: "2026-07-21T10:00:00Z",
  actorUid: "u1",
  actorEmail: "owner@yourcompany.com",
  action: "upsert_client",
  target: "a.com",
  before: { display_name: "Old" },
  after: { display_name: "New" },
};

describe("AuditPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { email: "a@x.com" };
    auth.loading = false;
    Object.assign(scope, { state: "platform", domains: [], isAdmin: true, isPlatform: true });
  });

  it("renders entries with who / what / target", async () => {
    mockAudit({ entries: [ENTRY], scanned: 1, scope: "platform" });
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText("upsert_client")).toBeTruthy());
    expect(screen.getByText("owner@yourcompany.com")).toBeTruthy();
    expect(screen.getByText("a.com")).toBeTruthy();
  });

  it("distinguishes 'nothing happened' from 'nothing concerns your tenant'", async () => {
    // Out-of-scope entries exist — say so, don't imply an empty platform.
    mockAudit({ entries: [], scanned: 42, scope: "tenant" });
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText(/42 entries exist platform-wide/i)).toBeTruthy());
  });

  it("says plainly when nothing has been recorded at all", async () => {
    mockAudit({ entries: [], scanned: 0, scope: "platform" });
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText(/no admin changes have been recorded/i)).toBeTruthy());
  });

  it("tells a tenant admin their view is scoped when rows are filtered out", async () => {
    mockAudit({ entries: [ENTRY], scanned: 10, scope: "tenant" });
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText(/of 10 platform-wide/i)).toBeTruthy());
  });

  it("surfaces a network failure with a retry (never-silent)", async () => {
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("offline"));
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText(/connection problem/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("explains a 403 as lacking access, not as an outage", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<AuditPage />);
    await waitFor(() => expect(screen.getByText(/don't have access to the audit trail/i)).toBeTruthy());
  });

  it("does not fetch for a non-admin", async () => {
    Object.assign(scope, { state: "none", isAdmin: false, isPlatform: false });
    render(<AuditPage />);
    expect(screen.getByText(/requires an admin group/i)).toBeTruthy();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("separates a scope-probe failure from a permissions denial", async () => {
    Object.assign(scope, { state: "error", isAdmin: false, isPlatform: false });
    render(<AuditPage />);
    expect(screen.getByText(/connection problem, not a permissions one/i)).toBeTruthy();
  });
});
