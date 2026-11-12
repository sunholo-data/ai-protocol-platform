import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
let auth: { user: { email: string } | null; loading: boolean } = { user: { email: "s@example.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in please</div> }));
const replace = vi.fn();
const router = { replace }; // stable, like Next's real router
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "doc-1" }), useRouter: () => router }));

import DocumentPage from "../page";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  fetchWithAuth.mockReset();
  replace.mockReset();
  auth = { user: { email: "s@example.com" }, loading: false };
});

describe("/documents/[id] (a link from Discord opens here)", () => {
  it("asks a signed-out visitor to sign in — the link itself grants nothing", () => {
    auth = { user: null, loading: false };
    render(<DocumentPage />);
    expect(screen.getByText("sign in please")).toBeTruthy();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("shows the owner their document and downloads it with their login", async () => {
    fetchWithAuth.mockResolvedValueOnce(json(200, { id: "doc-1", originalFilename: "Kickoff.docx", templateUsed: "house-style.docx" }));
    fetchWithAuth.mockResolvedValueOnce(new Response("x", { status: 200 }));
    globalThis.URL.createObjectURL = vi.fn(() => "blob:x");
    globalThis.URL.revokeObjectURL = vi.fn();
    render(<DocumentPage />);
    expect((await screen.findByTestId("doc-name")).textContent).toBe("Kickoff.docx");
    fireEvent.click(screen.getByText("Download"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Downloaded"));
    expect(fetchWithAuth).toHaveBeenLastCalledWith("/api/proxy/api/documents/doc-1/download");
  });

  it("opens a generated document in its skill's chat workbench, like the app does", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      json(200, { originalFilename: "Kickoff memo.docx", sourceFormat: "docx", skillId: "dw-1" }),
    );
    render(<DocumentPage />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/chat/dw-1?doc=doc-1&docName=Kickoff+memo.docx&docFormat=docx"),
    );
  });

  it("someone else's document is refused visibly", async () => {
    fetchWithAuth.mockResolvedValueOnce(json(403, {}));
    render(<DocumentPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("belongs to someone else");
  });

  it("a missing document says so", async () => {
    fetchWithAuth.mockResolvedValueOnce(json(404, {}));
    render(<DocumentPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("doesn't exist");
  });
});
