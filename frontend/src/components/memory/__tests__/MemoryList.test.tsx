// Your memory (docs/design/private/memory-screen.md): the list, delete-one and
// forget-everything, and every empty/error path visible (NEVER SILENT, #8).

import { render as rtlRender, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

type Handler = (url: string, init?: RequestInit) => Response;
let handler: Handler;
const calls: { url: string; method: string }[] = [];
function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET" });
    return handler(url, init);
  }),
}));

import { MemoryList } from "../MemoryList";
import { ConfirmProvider } from "@/components/ui/confirm";

// Deleting one memory asks through the shared confirm dialog (useConfirm).
const render = (ui: React.ReactElement) => rtlRender(ui, { wrapper: ConfirmProvider });

const LISTING = {
  available: true,
  truncated: false,
  memories: [
    { ref: "reasoningEngines/1/memories/a", fact: "Team is Northern region", updated_at: "2026-10-03T09:00:00Z" },
    { ref: "reasoningEngines/1/memories/b", fact: "Prefers Spanish", updated_at: null },
  ],
};

beforeEach(() => {
  calls.length = 0;
  handler = () => res(200, LISTING);
});

describe("MemoryList", () => {
  it("lists what is remembered", async () => {
    render(<MemoryList />);
    expect(await screen.findByText("Team is Northern region")).toBeInTheDocument();
    expect(screen.getByText("Prefers Spanish")).toBeInTheDocument();
    expect(calls[0].url).toBe("/api/proxy/api/me/memories");
  });

  it("asks for the project's memory when given a project", async () => {
    render(<MemoryList projectId="p1" />);
    await screen.findByText("Team is Northern region");
    expect(calls[0].url).toBe("/api/proxy/api/me/memories?project_id=p1");
  });

  it("says when nothing is remembered, and when memory is off", async () => {
    handler = () => res(200, { available: true, memories: [] });
    const { unmount } = render(<MemoryList />);
    expect(await screen.findByTestId("memory-empty")).toHaveTextContent("Nothing remembered yet.");
    unmount();
    handler = () => res(200, { available: false, memories: [] });
    render(<MemoryList />);
    expect(await screen.findByText("Memory isn't switched on in this workspace.")).toBeInTheDocument();
  });

  it("a failed load says why, with Retry", async () => {
    handler = () => res(502, { detail: { code: "MEMORY_ERROR", message: "Couldn't load your memory right now. Try again." } });
    render(<MemoryList />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load your memory right now.");
    handler = () => res(200, LISTING);
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Prefers Spanish")).toBeInTheDocument();
  });

  it("deletes one memory after confirming, sending its ref", async () => {
    handler = (url, init) => (init?.method === "DELETE" ? res(200, { deleted: 1 }) : res(200, LISTING));
    render(<MemoryList />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete: Prefers Spanish" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete this memory?" });
    expect(dialog).toHaveTextContent("Prefers Spanish");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Prefers Spanish")).toBeNull());
    expect(screen.getByTestId("memory-notice")).toHaveTextContent("Deleted.");
    const del = calls.find((c) => c.method === "DELETE");
    expect(del?.url).toBe(`/api/proxy/api/me/memories/item?ref=${encodeURIComponent("reasoningEngines/1/memories/b")}`);
  });

  it("a delete the server refuses keeps the memory and says why", async () => {
    handler = (url, init) =>
      init?.method === "DELETE" ? res(404, { detail: { message: "That memory wasn't found." } }) : res(200, LISTING);
    render(<MemoryList />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete: Prefers Spanish" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByTestId("memory-notice")).toHaveTextContent("That memory wasn't found.");
    expect(screen.getByText("Prefers Spanish")).toBeInTheDocument();
  });

  it("forget everything needs the typed word, then clears the list", async () => {
    let cleared = false;
    handler = (url, init) => {
      if (init?.method === "DELETE") {
        cleared = true;
        return res(200, { deleted: 2, refused: 0, failed: 0 });
      }
      return res(200, cleared ? { available: true, memories: [] } : LISTING);
    };
    render(<MemoryList />);
    fireEvent.click(await screen.findByRole("button", { name: "Forget everything…" }));
    const confirm = screen.getByRole("button", { name: "Forget everything" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Type forget to confirm"), { target: { value: "forget" } });
    fireEvent.click(confirm);
    expect(await screen.findByTestId("memory-empty")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/proxy/api/me/memories?confirm=forget");
  });
});
