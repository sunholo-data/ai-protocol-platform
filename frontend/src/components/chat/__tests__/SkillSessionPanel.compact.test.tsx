import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SkillSessionPanel } from "../SkillSessionPanel";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

const now = new Date().toISOString();
const row = (id: string, title: string): ChatSessionSummary =>
  ({
    session_id: id,
    skill_id: "s1",
    skill_label: "Research",
    title,
    last_message_at: now,
    is_owner: true,
  }) as ChatSessionSummary;

const sessions = Array.from({ length: 10 }, (_, i) => row(`c${i}`, `Chat number ${i}`));

describe("SkillSessionPanel compact rows (the sidebar)", () => {
  it("shows one line per chat, the first few, then Show more", async () => {
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={() => {}}
        compact
        initialCount={8}
        hrefFor={(s) => `/chat/x?session=${s.session_id}`}
      />,
    );
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.queryByText("Research")).toBeNull(); // no skill line
    expect(screen.getByRole("link", { name: "Chat number 0" })).toHaveAttribute("href", "/chat/x?session=c0");
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByRole("link")).toHaveLength(10);
  });

  it("filters by the sidebar's search and shows every match", () => {
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={() => {}}
        compact
        initialCount={2}
        query="number 9"
      />,
    );
    expect(screen.getByText("Chat number 9")).toBeInTheDocument();
    expect(screen.queryByText("Chat number 1")).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull(); // the sidebar owns the search box
  });

  it("renames in place from the row's menu", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    render(
      <SkillSessionPanel
        sessions={[row("c1", "Old title")]}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={() => {}}
        compact
        onRename={onRename}
        rowActions={(_s, { startRename }) => (
          <button type="button" onClick={startRename}>
            Rename
          </button>
        )}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Conversation name" });
    await userEvent.clear(input);
    await userEvent.type(input, "New title{Enter}");
    expect(onRename).toHaveBeenCalledWith("c1", "New title");
  });
});
