import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
const signIn = vi.fn(async () => {});
let auth: { user: { email: string } | null; loading: boolean; signIn: () => Promise<void> } = {
  user: { email: "owner@example.com" },
  loading: false,
  signIn,
};
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in please</div> }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "severa%3Aabc" }) }));

import ApprovePage from "../page";

const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const REQUEST = {
  id: "severa:abc",
  title: "Correct 17 time entries",
  review: "17 entries · 19.5 h\nEvery entry's billing label becomes Externally invoiced 2026-10-30.",
  risk: "routine",
  digest: "0123456789abcdef".repeat(4),
  expires_at: 1_900_000_000,
  state: "pending",
  session_id: "s1",
};
const DECISION_URL = "/api/proxy/api/approvals/severa%3Aabc/decision";

beforeEach(() => {
  fetchWithAuth.mockReset();
  signIn.mockClear();
  auth = { user: { email: "owner@example.com" }, loading: false, signIn };
});

describe("approval page", () => {
  it("asks a signed-out visitor to sign in before showing anything", () => {
    auth = { ...auth, user: null };
    render(<ApprovePage />);
    expect(screen.getByText("sign in please")).toBeTruthy();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("shows the exact review and approves with the digest it showed", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply(REQUEST));
    fetchWithAuth.mockResolvedValueOnce(reply({ state: "completed", message: "Saved and verified 17 entries." }));
    render(<ApprovePage />);
    expect(await screen.findByText("Correct 17 time entries")).toBeTruthy();
    expect(screen.getByTestId("approval-review").textContent).toContain("Externally invoiced 2026-10-30");
    expect(screen.getByText(/0123456789ab/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Saved and verified 17 entries.")).toBeTruthy();
    expect(fetchWithAuth).toHaveBeenLastCalledWith(DECISION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: "approve", digest: REQUEST.digest }),
    });
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("declines without doing it", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply(REQUEST));
    fetchWithAuth.mockResolvedValueOnce(reply({ state: "cancelled", message: "Cancelled. Nothing was changed." }));
    render(<ApprovePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline" }));
    expect(await screen.findByText("Cancelled. Nothing was changed.")).toBeTruthy();
    expect(JSON.parse(fetchWithAuth.mock.calls[1][1].body).decision).toBe("cancel");
  });

  it("says plainly when the approval is missing or not yours", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply({ detail: "No such approval." }, 404));
    render(<ApprovePage />);
    expect(await screen.findByText(/no approval here for you/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("shows a decided or expired request without buttons", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply({ ...REQUEST, state: "completed" }));
    render(<ApprovePage />);
    expect(await screen.findByText(/already completed/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("reloads the review when it changed after you opened it", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply(REQUEST));
    fetchWithAuth.mockResolvedValueOnce(reply({ detail: "This approval changed after you reviewed it." }, 409));
    fetchWithAuth.mockResolvedValueOnce(reply({ ...REQUEST, review: "New review", digest: "f".repeat(64) }));
    render(<ApprovePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByTestId("approval-review").textContent).toContain("New review"));
    expect(screen.getByRole("alert").textContent).toMatch(/changed after you opened it/i);
  });

  it("offers a fresh sign-in when a high-risk approval needs one, then retries", async () => {
    fetchWithAuth.mockResolvedValueOnce(reply({ ...REQUEST, risk: "high" }));
    fetchWithAuth.mockResolvedValueOnce(reply({ detail: "This action needs a recent sign-in." }, 403));
    fetchWithAuth.mockResolvedValueOnce(reply({ state: "completed", message: "Sent." }));
    render(<ApprovePage />);
    expect(await screen.findByText(/high-risk/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign in again and approve" }));
    expect(await screen.findByText("Sent.")).toBeTruthy();
    expect(signIn).toHaveBeenCalledOnce();
  });
});
