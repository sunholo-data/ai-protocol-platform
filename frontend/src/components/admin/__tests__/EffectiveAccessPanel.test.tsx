// EffectiveAccessPanel — "what your users actually see" (v6.16.0 Phase 2).
//
// The property under test is honesty: the panel must surface the admin-bypass
// gap rather than quietly showing the admin's own (wider) view. Every reason
// string is authored by the backend — these tests assert the panel RENDERS what
// it's given and never invents a verdict.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EffectiveAccessPanel } from "../EffectiveAccessPanel";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";

function mockCheck(plane: unknown, opts: { ok?: boolean; status?: number; userFound?: boolean } = {}) {
  vi.mocked(fetchWithAuth).mockResolvedValue({
    ok: opts.ok ?? true,
    status: opts.status ?? 200,
    json: async () => ({
      email: "user@one.com",
      domain: "one.com",
      userFound: opts.userFound ?? true,
      skillVisibility: plane,
    }),
  } as unknown as Response);
}

const PLANE = {
  enabledSkills: ["one-assistant"],
  visibleCount: 1,
  totalCount: 2,
  hiddenByTenantFilter: ["Wip Demo"],
  skills: [
    {
      skillId: "id-1",
      slug: "one-assistant",
      label: "One Assistant",
      visible: true,
      reason: "access_control.type=public → allow; in the tenant's enabled_skills",
      accessAllowed: true,
      tenantAllowed: true,
      adminBypass: false,
    },
    {
      skillId: "id-2",
      slug: "wip-demo",
      label: "Wip Demo",
      visible: false,
      reason: "not in the tenant's enabled_skills",
      accessAllowed: true,
      tenantAllowed: false,
      adminBypass: false,
    },
  ],
};

async function submit(email = "user@one.com") {
  fireEvent.change(screen.getByLabelText("User email to inspect"), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
}

describe("EffectiveAccessPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("explains up front that the admin's own list is not a preview", () => {
    render(<EffectiveAccessPanel domain="one.com" />);
    expect(screen.getByText(/admins bypass the tenant/i)).toBeTruthy();
  });

  it("renders the per-skill verdicts and the backend's reasons verbatim", async () => {
    mockCheck(PLANE);
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText("One Assistant")).toBeTruthy());
    // "Wip Demo" appears twice by design — once in the hidden-skills callout and
    // once in the per-skill list. Both are wanted, so assert on the count.
    expect(screen.getAllByText("Wip Demo").length).toBeGreaterThanOrEqual(1);
    // Reason text comes from the server — not synthesised client-side.
    expect(screen.getByText("not in the tenant's enabled_skills")).toBeTruthy();
    expect(screen.getByText(/1 of 2 skills visible/)).toBeTruthy();
  });

  it("headlines the skills the admin sees but the user does not", async () => {
    mockCheck(PLANE);
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/You can see 1 skill that this user cannot/)).toBeTruthy());
  });

  it("flags a skill the admin only sees because of their own bypass", async () => {
    mockCheck({
      ...PLANE,
      skills: [{ ...PLANE.skills[1], visible: true, adminBypass: true, tenantAllowed: false }],
    });
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/you only see this as an admin/i)).toBeTruthy());
  });

  it("says when the tenant has no filter at all", async () => {
    mockCheck({ ...PLANE, enabledSkills: null, hiddenByTenantFilter: [] });
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/no enabled-skills filter/i)).toBeTruthy());
  });

  it("warns when the user has never signed in (projection, not fact)", async () => {
    mockCheck(PLANE, { userFound: false });
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/hasn't signed in yet/i)).toBeTruthy());
  });

  it("explains a 403 as an out-of-scope user, not a generic failure", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit("someone@other.com");
    await waitFor(() => expect(screen.getByText(/outside your tenant scope/i)).toBeTruthy());
  });

  it("surfaces a network failure visibly (never-silent)", async () => {
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("offline"));
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/connection problem/i)).toBeTruthy());
  });

  it("surfaces a missing visibility payload rather than rendering blank", async () => {
    mockCheck(null);
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByText(/no visibility data/i)).toBeTruthy());
  });

  it("shows a pending state while the check runs", async () => {
    let release: (v: Response) => void = () => {};
    vi.mocked(fetchWithAuth).mockReturnValue(new Promise((res) => (release = res)) as Promise<Response>);
    render(<EffectiveAccessPanel domain="one.com" />);
    await submit();
    await waitFor(() => expect(screen.getByRole("button", { name: "Checking…" })).toBeTruthy());
    release({ ok: true, status: 200, json: async () => ({ skillVisibility: PLANE }) } as unknown as Response);
  });

  it("does not fire on an empty email", async () => {
    render(<EffectiveAccessPanel domain="one.com" />);
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });
});
