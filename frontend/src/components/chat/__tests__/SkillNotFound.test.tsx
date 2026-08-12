import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockSignOut = vi.fn();
const mockUseAuth = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { SkillNotFound } from "../SkillNotFound";

const fetchMock = vi.mocked(fetchWithAuth);

beforeEach(() => {
  fetchMock.mockReset();
  mockSignOut.mockReset();
  mockUseAuth.mockReturnValue({
    user: { email: "wrong-user@example.com", uid: "u1" },
    signOut: mockSignOut,
  });
});

describe("SkillNotFound", () => {
  it("shows the requested slug + 'Skill not found' eyebrow + sign-out CTA", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ email: "wrong-user@example.com", groupTags: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<SkillNotFound slug="one-ppa-expert" />);
    expect(screen.getByText(/Skill not found/i)).toBeInTheDocument();
    expect(screen.getByText("one-ppa-expert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("wrong-user@example.com")).toBeInTheDocument();
    });
  });

  it("surfaces the user's groupTags from /api/auth/whoami so they can see what access they have", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ email: "alice@example.com", groupTags: ["workshop-attendee"] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<SkillNotFound slug="one-ppa-expert" />);
    await waitFor(() => {
      expect(screen.getByText("workshop-attendee")).toBeInTheDocument();
    });
  });

  it("triggers signOut() when the sign-out button is clicked", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ email: "u@x.com", groupTags: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<SkillNotFound slug="one-ppa-expert" />);
    screen.getByRole("button", { name: /sign out/i }).click();
    expect(mockSignOut).toHaveBeenCalledOnce();
  });
});
