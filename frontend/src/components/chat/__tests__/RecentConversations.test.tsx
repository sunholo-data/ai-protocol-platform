import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { RecentConversations } from "../RecentConversations";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

function makeSession(overrides: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return {
    session_id: "sess-1",
    skill_id: "skill-x",
    owner_uid: "u1",
    title: "Compare EDP and LEAP",
    turn_count: 4,
    first_message_at: new Date(Date.now() - 7_200_000).toISOString(),
    last_message_at: new Date(Date.now() - 3_600_000).toISOString(),
    archived_at: null,
    document_ids: [],
    is_owner: true,
    skill_label: "PPA Expert",
    ...overrides,
  };
}

describe("RecentConversations (fresh-chat landing)", () => {
  it("renders nothing for a user with no history", () => {
    const { container } = render(<RecentConversations sessions={[]} onSelectSession={vi.fn()} onShowAll={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("lists the latest conversations with agent and time, and continues one on click", () => {
    const onSelect = vi.fn();
    render(<RecentConversations sessions={[makeSession()]} onSelectSession={onSelect} onShowAll={vi.fn()} />);
    expect(screen.getByText("PPA Expert · 1h ago")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Compare EDP and LEAP/ }));
    expect(onSelect).toHaveBeenCalledWith("sess-1");
  });

  it("caps the list and opens the full history from View all", () => {
    const onShowAll = vi.fn();
    const sessions = Array.from({ length: 8 }, (_, i) => makeSession({ session_id: `s${i}`, title: `Chat ${i}` }));
    render(<RecentConversations sessions={sessions} onSelectSession={vi.fn()} onShowAll={onShowAll} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "View all" }));
    expect(onShowAll).toHaveBeenCalled();
  });

  it("never offers a conversation whose messages are gone", () => {
    render(
      <RecentConversations
        sessions={[makeSession({ title: "Lost one", transcript_lost: true }), makeSession({ session_id: "ok", title: "Fine one" })]}
        onSelectSession={vi.fn()}
        onShowAll={vi.fn()}
      />,
    );
    expect(screen.queryByText("Lost one")).not.toBeInTheDocument();
    expect(screen.getByText("Fine one")).toBeInTheDocument();
  });
});
