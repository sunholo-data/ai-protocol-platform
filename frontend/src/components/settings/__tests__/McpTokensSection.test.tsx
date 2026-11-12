import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
let auth: { user: { email: string } | null; loading: boolean } = { user: { email: "s@example.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in please</div> }));

import { McpTokensSection } from "../McpTokensSection";

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const TOKEN = { id: "abcdef012345", name: "Claude Code", createdAt: null, expiresAt: "2027-01-10T00:00:00Z", lastUsedAt: null };

beforeEach(() => {
  fetchWithAuth.mockReset();
  auth = { user: { email: "s@example.com" }, loading: false };
  document.title = "Platform";
});

describe("Settings → Connections (MCP tokens)", () => {
  it("creates a token and shows it once with a ready-to-paste command", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fetchWithAuth.mockResolvedValueOnce(ok({ ...TOKEN, token: "aip_mcp_secret" }));
    fetchWithAuth.mockResolvedValueOnce(ok([TOKEN]));
    render(<McpTokensSection />);
    fireEvent.click(await screen.findByText("Create token"));
    const box = await screen.findByTestId("new-token");
    expect(box.textContent).toContain("claude mcp add --transport http platform");
    expect(box.textContent).toContain("/api/proxy/mcp");
    expect(box.textContent).toContain("Bearer aip_mcp_secret");
    expect((await screen.findByTestId("token-list")).textContent).toContain("Claude Code");
  });

  it("revokes a token after confirming", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchWithAuth.mockResolvedValueOnce(ok([TOKEN]));
    fetchWithAuth.mockResolvedValueOnce(ok({ ok: true }));
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    render(<McpTokensSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke Claude Code" }));
    await screen.findByText("None yet.");
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/mcp/tokens/abcdef012345", { method: "DELETE" });
    expect(confirm.mock.calls[0][0]).toMatch(/Revoke this token\?/);
    confirm.mockRestore();
  });

  it("mints approval rights only when the box is ticked, and says what they allow", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fetchWithAuth.mockResolvedValueOnce(ok({ ...TOKEN, scopes: ["mcp"], token: "aip_mcp_plain" }));
    fetchWithAuth.mockResolvedValueOnce(ok([TOKEN]));
    render(<McpTokensSection />);
    fireEvent.click(await screen.findByText("Create token"));
    await screen.findByTestId("new-token");
    expect(JSON.parse(fetchWithAuth.mock.calls[1][1].body)).toEqual({ name: "Claude Code", approve: false });

    const box = screen.getByRole("checkbox", { name: /can approve actions/i }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(screen.getByTestId("approve-warning").textContent).toMatch(/anything holding this token can approve/i);
    fetchWithAuth.mockResolvedValueOnce(ok({ ...TOKEN, scopes: ["mcp", "approve"], token: "aip_mcp_approver" }));
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fireEvent.click(screen.getByText("Create token"));
    await waitFor(() => expect(screen.getByTestId("new-token").textContent).toContain("aip_mcp_approver"));
    expect(JSON.parse(fetchWithAuth.mock.calls[3][1].body)).toEqual({ name: "Claude Code", approve: true });
    expect(screen.getByTestId("new-token").textContent).toMatch(/can approve routine actions/i);
  });

  it("marks tokens that can approve", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([{ ...TOKEN, scopes: ["mcp", "approve"] }, { ...TOKEN, id: "x2", name: "Desktop", scopes: ["mcp"] }]));
    render(<McpTokensSection />);
    const items = (await screen.findByTestId("token-list")).querySelectorAll("li");
    expect(items[0].textContent).toContain("Can approve");
    expect(items[1].textContent).not.toContain("Can approve");
  });

  it("never fails silently, and shows the server's reason", async () => {
    fetchWithAuth.mockResolvedValueOnce(ok([]));
    fetchWithAuth.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "You already have 10 tokens." }), { status: 400 }));
    render(<McpTokensSection />);
    fireEvent.click(await screen.findByText("Create token"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("already have 10 tokens"));
  });
});
