// Admin hub IA — five task-shaped areas (v6.16.0 Phase 3).
//
// The previous layout had one card per API surface, which mirrored the
// backend's module structure rather than anything an operator asks. These tests
// pin the two properties that matter: the areas are named for questions, and a
// tenant admin never sees a platform-only door that would 403 them.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminHub from "../page";

const auth: { user: unknown; loading: boolean } = { user: { email: "u@x.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));

const recheck = vi.fn(async () => {});
const scope: {
  state: string;
  domains: string[];
  isAdmin: boolean;
  isPlatform: boolean;
  recheck: () => Promise<void>;
} = { state: "platform", domains: [], isAdmin: true, isPlatform: true, recheck };
vi.mock("@/hooks/useAdminScope", () => ({ useAdminScope: () => scope }));

vi.mock("@/components/chat/SignInRequired", () => ({
  SignInRequired: () => <div>sign in required</div>,
}));

function asPlatform() {
  Object.assign(scope, { state: "platform", domains: [], isAdmin: true, isPlatform: true });
}
function asTenant(domain = "one.com") {
  Object.assign(scope, { state: "tenant", domains: [domain], isAdmin: true, isPlatform: false });
}

describe("AdminHub IA", () => {
  beforeEach(() => {
    auth.user = { email: "u@x.com" };
    auth.loading = false;
    asPlatform();
  });

  it("renders five task-shaped areas for a platform admin", async () => {
    render(<AdminHub />);
    for (const title of ["Your tenant", "People & access", "Skills", "Activity & audit", "Platform"]) {
      expect(screen.getByText(title)).toBeTruthy();
    }
  });

  it("names the question each area answers, not the API it wraps", () => {
    render(<AdminHub />);
    expect(screen.getByText(/what do my users actually see/i)).toBeTruthy();
    expect(screen.getByText(/who is here, and what may they use/i)).toBeTruthy();
  });

  it("surfaces the effective-access entry point", () => {
    render(<AdminHub />);
    expect(screen.getByText("What your users actually see")).toBeTruthy();
  });

  it("hides the Platform area from a tenant admin (no door that always slams)", () => {
    asTenant();
    render(<AdminHub />);
    expect(screen.queryByText("Platform")).toBeNull();
    // ...but the areas they CAN use are still there.
    expect(screen.getByText("Your tenant")).toBeTruthy();
    expect(screen.getByText("People & access")).toBeTruthy();
  });

  it("tells a tenant admin their view is scoped, so a short list isn't confusing", () => {
    asTenant("acmeenergy.com");
    render(<AdminHub />);
    expect(screen.getByText(/scoped to acmeenergy\.com/i)).toBeTruthy();
  });

  it("keeps every existing admin route reachable", () => {
    render(<AdminHub />);
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    for (const route of [
      "/admin/tenants",
      "/admin/users",
      "/admin/groups",
      "/admin/tool-permissions",
      "/admin/analytics",
      "/admin/audit",
      "/admin/settings",
      "/skills/studio/new",
    ]) {
      expect(hrefs).toContain(route);
    }
  });

  it("shows a distinct error state rather than implying no access", async () => {
    Object.assign(scope, { state: "error", domains: [], isAdmin: false, isPlatform: false });
    render(<AdminHub />);
    await waitFor(() => expect(screen.getByText(/couldn't check your access/i)).toBeTruthy());
    expect(screen.queryByText("Admins only")).toBeNull();
  });

  it("shows 'Admins only' for a genuine non-admin", () => {
    Object.assign(scope, { state: "none", domains: [], isAdmin: false, isPlatform: false });
    render(<AdminHub />);
    expect(screen.getByText("Admins only")).toBeTruthy();
  });

  it("offers a token re-check, because a just-granted tag isn't in the current token", () => {
    // Without this the newly-appointed admin sees no link, no error, and no
    // explanation for ~1h — a silent failure, not a permissions one.
    Object.assign(scope, { state: "none", domains: [], isAdmin: false, isPlatform: false });
    render(<AdminHub />);
    expect(screen.getByText(/only lands when your sign-in token refreshes/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /re-check my access/i }));
    expect(recheck).toHaveBeenCalled();
  });
});
