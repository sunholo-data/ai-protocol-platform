import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import { SharedSessionsSection } from "../SharedSessionsSection";

function shared(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "daneel-sess-1",
    document_ids: [],
    skill_id: "skill-daneel",
    owner_uid: "machine-daneel",
    title: "Weekly inbox triage",
    turn_count: 4,
    first_message_at: new Date(Date.now() - 7_200_000).toISOString(),
    last_message_at: new Date(Date.now() - 120_000).toISOString(),
    archived_at: null,
    is_owner: false,
    skill_label: "Inbox Helper",
    owner_name: "Daneel",
    can_reply: true,
    ...overrides,
  };
}

function respond(sessions: object[]) {
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ sessions, next_cursor: null }),
  } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("SharedSessionsSection", () => {
  it("requests the shared list", async () => {
    respond([]);
    render(<SharedSessionsSection activeSessionId={null} onSelectSession={vi.fn()} />);
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/proxy/api/sessions?shared=true&page_size=30",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
  });

  it("renders nothing when no conversations are shared", async () => {
    respond([]);
    const { container } = render(
      <SharedSessionsSection activeSessionId={null} onSelectSession={vi.fn()} />,
    );
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Shared with me")).not.toBeInTheDocument();
  });

  it("renders rows with the owner's friendly name, agent label and title", async () => {
    respond([shared(), shared({ session_id: "daneel-sess-2", title: null })]);
    render(<SharedSessionsSection activeSessionId={null} onSelectSession={vi.fn()} />);

    expect(await screen.findByText("Shared with me")).toBeInTheDocument();
    expect(screen.getByText("Weekly inbox triage")).toBeInTheDocument();
    // Untitled fallback matches own sessions — never a raw id.
    expect(screen.getByText("Untitled chat")).toBeInTheDocument();
    expect(screen.getAllByTestId("session-owner-name")[0]).toHaveTextContent("Daneel");
    expect(screen.getAllByTestId("session-agent-label")[0]).toHaveTextContent("Inbox Helper");
    expect(screen.queryByText("machine-daneel")).not.toBeInTheDocument();
    // Not the owner → no delete affordance.
    expect(screen.queryByLabelText(/^Delete /)).not.toBeInTheDocument();
  });

  it("omits the owner line when owner_name is unknown (older backend)", async () => {
    const row = shared();
    delete (row as Record<string, unknown>).owner_name;
    respond([row]);
    render(<SharedSessionsSection activeSessionId={null} onSelectSession={vi.fn()} />);
    expect(await screen.findByText("Weekly inbox triage")).toBeInTheDocument();
    expect(screen.queryByTestId("session-owner-name")).not.toBeInTheDocument();
  });

  it("opens the session on click, like an own session", async () => {
    respond([shared()]);
    const onSelect = vi.fn();
    render(<SharedSessionsSection activeSessionId={null} onSelectSession={onSelect} />);
    await userEvent.click(await screen.findByText("Weekly inbox triage"));
    expect(onSelect).toHaveBeenCalledWith("daneel-sess-1", expect.any(String));
  });

  it("shows a visible error with a working retry when loading fails", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 } as Response);
    render(<SharedSessionsSection activeSessionId={null} onSelectSession={vi.fn()} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/couldn.t load conversations shared with you/i);

    respond([shared()]);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Weekly inbox triage")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
