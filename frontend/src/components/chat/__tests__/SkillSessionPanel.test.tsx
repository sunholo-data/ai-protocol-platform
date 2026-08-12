import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { SkillSessionPanel } from "../SkillSessionPanel";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

function makeSession(overrides: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return {
    session_id: "sess-1",
    skill_id: "skill-x",
    owner_uid: "u1",
    title: "Test session",
    turn_count: 2,
    first_message_at: new Date(Date.now() - 3_600_000).toISOString(),
    last_message_at: new Date(Date.now() - 60_000).toISOString(),
    archived_at: null,
    document_ids: [],
    is_owner: true,
    ...overrides,
  };
}

describe("SkillSessionPanel", () => {
  it("renders session titles", () => {
    const sessions = [
      makeSession({ session_id: "sess-1", title: "First session" }),
      makeSession({ session_id: "sess-2", title: "Second session" }),
    ];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={vi.fn()}
      />,
    );
    expect(screen.getByText("First session")).toBeInTheDocument();
    expect(screen.getByText("Second session")).toBeInTheDocument();
  });

  it("marks the active session with aria-current", () => {
    const sessions = [
      makeSession({ session_id: "sess-1", title: "Active" }),
      makeSession({ session_id: "sess-2", title: "Inactive" }),
    ];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId="sess-1"
        isLoading={false}
        onSelectSession={vi.fn()}
      />,
    );
    const activeBtn = screen.getByText("Active").closest("button");
    expect(activeBtn).toHaveAttribute("aria-current", "true");
    const inactiveBtn = screen.getByText("Inactive").closest("button");
    expect(inactiveBtn).not.toHaveAttribute("aria-current");
  });

  it("calls onSelectSession with the session id when clicked", async () => {
    const onSelect = vi.fn();
    const sessions = [makeSession({ session_id: "sess-abc", title: "Click me" })];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={onSelect}
      />,
    );
    await userEvent.click(screen.getByText("Click me"));
    expect(onSelect).toHaveBeenCalledWith("sess-abc");
  });

  it("shows loading skeleton when isLoading is true", () => {
    render(
      <SkillSessionPanel
        sessions={[]}
        activeSessionId={null}
        isLoading={true}
        onSelectSession={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Loading sessions")).toBeInTheDocument();
  });

  it("shows empty state when no sessions", () => {
    render(
      <SkillSessionPanel
        sessions={[]}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={vi.fn()}
      />,
    );
    expect(screen.getByText(/no previous sessions/i)).toBeInTheDocument();
  });

  it("falls back to session ID prefix when title is null", () => {
    const sessions = [makeSession({ session_id: "abcdef12-xxxx", title: null })];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={vi.fn()}
      />,
    );
    expect(screen.getByText(/Session abcdef12/)).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // session-delete on the SKILL-level panel (extending 1.17 to the left sidebar)
  // ---------------------------------------------------------------------------

  it("renders a trash button on owner rows when onDelete is provided", () => {
    const sessions = [
      makeSession({ session_id: "s-own", title: "Mine", is_owner: true }),
      makeSession({ session_id: "s-team", title: "Theirs", is_owner: false }),
    ];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: /delete mine/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /delete theirs/i }),
    ).toBeNull();
  });

  it("does not render any trash button when onDelete is omitted", () => {
    const sessions = [makeSession({ session_id: "s-own", title: "Mine", is_owner: true })];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /delete mine/i }),
    ).toBeNull();
  });

  it("calls onDelete with the session id when the trash button is clicked, without selecting the row", async () => {
    const onDelete = vi.fn();
    const onSelect = vi.fn();
    const sessions = [makeSession({ session_id: "s-target", title: "Goodbye", is_owner: true })];
    render(
      <SkillSessionPanel
        sessions={sessions}
        activeSessionId={null}
        isLoading={false}
        onSelectSession={onSelect}
        onDelete={onDelete}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /delete goodbye/i }));
    expect(onDelete).toHaveBeenCalledWith("s-target");
    // Critical: clicking the trash must not also fire row-selection (would
    // navigate to the very session we're deleting, breaking active-session
    // URL clear logic in the parent).
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe("SkillSessionPanel — cross-skill history (2026-08-05)", () => {
  // Switching agent via the top bar starts a new session on the new skill, so a
  // sitting that moved between agents is split into one session per skill. The
  // list is cross-skill now; each row says which agent it was with, and the
  // filter narrows to one.
  const base = {
    activeSessionId: null,
    isLoading: false,
    onSelectSession: vi.fn(),
  };

  it("labels each row with its agent's friendly name", () => {
    render(
      <SkillSessionPanel
        {...base}
        sessions={[
          makeSession({ session_id: "a", skill_id: "s-ppa", skill_label: "Contract Expert" }),
          makeSession({ session_id: "b", skill_id: "s-oai", skill_label: "OpenAI Reasoner" }),
        ]}
      />,
    );

    const labels = screen.getAllByTestId("session-agent-label").map((n) => n.textContent);
    expect(labels).toEqual(["Contract Expert", "OpenAI Reasoner"]);
  });

  it("never falls back to printing a raw skill id", () => {
    render(
      <SkillSessionPanel
        {...base}
        sessions={[makeSession({ skill_id: "db8c5ee2-5c96-4a65", skill_label: null })]}
      />,
    );

    expect(screen.queryByTestId("session-agent-label")).not.toBeInTheDocument();
    expect(screen.queryByText(/db8c5ee2/)).not.toBeInTheDocument();
  });

  it("offers one filter option per distinct agent, by friendly name", () => {
    render(
      <SkillSessionPanel
        {...base}
        sessions={[
          makeSession({ session_id: "a", skill_id: "s-ppa", skill_label: "Contract Expert" }),
          makeSession({ session_id: "b", skill_id: "s-ppa", skill_label: "Contract Expert" }),
          makeSession({ session_id: "c", skill_id: "s-oai", skill_label: "OpenAI Reasoner" }),
        ]}
        skillFilter={null}
        onFilterChange={vi.fn()}
      />,
    );

    const options = screen
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).textContent);
    expect(options).toEqual(["All agents", "Contract Expert", "OpenAI Reasoner"]);
  });

  it("reports the chosen agent, and null for 'All agents'", async () => {
    const onFilterChange = vi.fn();
    render(
      <SkillSessionPanel
        {...base}
        sessions={[
          makeSession({ session_id: "a", skill_id: "s-ppa", skill_label: "Contract Expert" }),
          makeSession({ session_id: "c", skill_id: "s-oai", skill_label: "OpenAI Reasoner" }),
        ]}
        skillFilter={null}
        onFilterChange={onFilterChange}
      />,
    );

    const select = screen.getByLabelText("Filter conversations by agent");
    await userEvent.selectOptions(select, "s-oai");
    expect(onFilterChange).toHaveBeenCalledWith("s-oai");

    await userEvent.selectOptions(select, "");
    expect(onFilterChange).toHaveBeenCalledWith(null);
  });

  it("keeps the filter visible when it matches nothing, so the user can get back", () => {
    // Filtering to an agent with no conversations must not strand the user with
    // an empty panel and no control to clear it.
    render(
      <SkillSessionPanel
        {...base}
        sessions={[]}
        skillFilter="s-ppa"
        onFilterChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Filter conversations by agent")).toBeInTheDocument();
    expect(screen.getByText("No conversations with this agent")).toBeInTheDocument();
  });

  it("hides the filter when there is nothing to filter", () => {
    render(
      <SkillSessionPanel
        {...base}
        sessions={[makeSession({ skill_id: "s-ppa", skill_label: "Contract Expert" })]}
        skillFilter={null}
        onFilterChange={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Filter conversations by agent")).not.toBeInTheDocument();
  });
});

// --- v6.23.0 B5 Phase 2: dead-transcript rows ------------------------------
//
// 14 of the 100 most recent sessions on test (6 of them ONE's) have a mirror row
// with real turns but no canonical transcript — casualties of the SessionManager
// sweep fixed on 2026-08-05. Opening one already says "messages no longer
// available"; the point of these tests is that the LIST says it too, so a user
// doesn't have to click a dead row to discover it is dead.

describe("SkillSessionPanel — lost transcripts (B5)", () => {
  it("labels a session whose transcript is gone", () => {
    const s = makeSession({ title: "PPA review", transcript_lost: true });
    render(<SkillSessionPanel sessions={[s]} activeSessionId={null} onSelectSession={() => {}} isLoading={false} />);
    expect(screen.getByTestId("session-transcript-lost").textContent).toMatch(/unavailable/i);
  });

  it("does NOT label a healthy session, and keeps showing its agent", () => {
    const s = makeSession({ skill_label: "ONE Assistant", transcript_lost: false });
    render(<SkillSessionPanel sessions={[s]} activeSessionId={null} onSelectSession={() => {}} isLoading={false} />);
    expect(screen.queryByTestId("session-transcript-lost")).toBeNull();
    expect(screen.getByTestId("session-agent-label").textContent).toBe("ONE Assistant");
  });

  it("keeps a dead row selectable — honest, not hidden", async () => {
    // Deliberately NOT disabled or filtered out: the row carries title/date
    // metadata worth reading, new messages in it DO still save, and dropping it
    // from the list would silently delete history from the user's view.
    const onSelect = vi.fn();
    const s = makeSession({ session_id: "dead-1", title: "PPA review", transcript_lost: true });
    render(<SkillSessionPanel sessions={[s]} activeSessionId={null} onSelectSession={onSelect} isLoading={false} />);
    await userEvent.click(screen.getByTitle("PPA review"));
    expect(onSelect).toHaveBeenCalledWith("dead-1");
  });
});
