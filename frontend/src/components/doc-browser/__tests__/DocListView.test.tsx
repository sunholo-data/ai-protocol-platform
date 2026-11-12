import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { useDocBrowser } from "@/hooks/useDocBrowser";
import { DocListView } from "../DocListView";

type State = ReturnType<typeof useDocBrowser>;
let state: State;
vi.mock("@/hooks/useDocBrowser", () => ({ useDocBrowser: () => state }));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/components/document/DocumentPreview", () => ({
  DocumentPreview: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function base(over: Partial<State> = {}): State {
  return {
    folders: [],
    activeFolderId: null,
    documents: [],
    searchQuery: "",
    filteredDocuments: [],
    setActiveFolderId: vi.fn(),
    setSearchQuery: vi.fn(),
    loading: false,
    error: null,
    retry: vi.fn(),
    ...over,
  };
}

describe("DocListView", () => {
  beforeEach(() => {
    state = base();
  });

  it("shows the empty state when there really are no documents", () => {
    render(<DocListView uid="u1" />);
    expect(screen.getByText(/No documents yet/)).toBeInTheDocument();
  });

  it("shows a loading line, not the empty state, while the API loads", () => {
    state = base({ loading: true });
    render(<DocListView uid="u1" />);
    expect(screen.getByText(/Loading documents/)).toBeInTheDocument();
    expect(screen.queryByText(/No documents yet/)).not.toBeInTheDocument();
  });

  it("renders a load error visibly with a Retry (NEVER SILENT)", async () => {
    const retry = vi.fn();
    state = base({ error: "Couldn't load your folders (HTTP 500).", retry });
    render(<DocListView uid="u1" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your folders (HTTP 500).");
    expect(screen.queryByText(/No documents yet/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("nests an uploaded folder's subfolders and opens the path to the selected one", async () => {
    const folder = (id: string, name: string, docCount = 1) => ({ id, name, userId: "u1", docCount, parsedCount: docCount });
    const setActiveFolderId = vi.fn();
    state = base({
      folders: [folder("f-2026", "flying-fish/survey/2026"), folder("f-notes", "flying-fish/notes", 3), folder("f-other", "Contracts")],
      activeFolderId: "f-2026",
      setActiveFolderId,
    });
    render(<DocListView uid="u1" />);

    // One top-level row for the uploaded folder, not one per path.
    expect(screen.queryByText("flying-fish/notes")).toBeNull();
    const top = screen.getByRole("button", { name: /^flying-fish/ });
    expect(top).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^survey/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^2026/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^Contracts/ })).toHaveAttribute("aria-expanded", "false");
    // The parent-only row counts the documents beneath it.
    expect(top).toHaveTextContent("4");

    await userEvent.click(screen.getByRole("button", { name: /^notes/ }));
    expect(setActiveFolderId).toHaveBeenCalledWith("f-notes");

    await userEvent.click(top);
    expect(screen.queryByRole("button", { name: /^survey/ })).toBeNull();
  });
});
