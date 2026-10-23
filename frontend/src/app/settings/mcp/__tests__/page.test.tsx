import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
let auth: { user: { email: string } | null; loading: boolean } = { user: { email: "s@example.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in please</div> }));

import McpTokensPage from "../page";

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const TOKEN = { id: "abcdef012345", name: "Claude Code", createdAt: null, expiresAt: "2027-01-10T00:00:00Z", lastUsedAt: null };

beforeEach(() => {
  fetchWithAuth.mockReset();
  auth = { user: { email: "s@example.com" }, loading: false };
  document.title = "Platform";
});

describe("MCP tokens page", () => {
  it("asks a signed-out visitor to sign in", () => {
    auth = { user: null, loading: false };
    render(<McpTokensPage />);
    expect(screen.getByText("sign in please")).toBeTruthy();
  });

  it("creates a token and shows it once with a ready-to-paste command", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fetchWithAuth.mockResolvedValueOnce(ok({ ...TOKEN, token: "aip_mcp_secret" }));
    fetchWithAuth.mockResolvedValueOnce(ok([TOKEN]));
    render(<McpTokensPage />);
    fireEvent.click(await screen.findByText("Create token"));
    const box = await screen.findByTestId("new-token");
    expect(box.textContent).toContain("claude mcp add --transport http platform");
    expect(box.textContent).toContain("/api/proxy/mcp");
    expect(box.textContent).toContain("Bearer aip_mcp_secret");
    expect((await screen.findByTestId("token-list")).textContent).toContain("Claude Code");
  });

  it("revokes a token", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([TOKEN]));
    fetchWithAuth.mockResolvedValueOnce(ok({ ok: true }));
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    render(<McpTokensPage />);
    fireEvent.click(await screen.findByText("Revoke"));
    await screen.findByText("None yet.");
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/mcp/tokens/abcdef012345", { method: "DELETE" });
  });

  it("never fails silently, and shows the server's reason", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fetchWithAuth.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "You already have 10 tokens." }), { status: 400 }));
    render(<McpTokensPage />);
    fireEvent.click(await screen.findByText("Create token"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("already have 10 tokens"));
  });
});
