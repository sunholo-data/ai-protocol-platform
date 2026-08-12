// Admin analytics — session browser + chronological trace timeline.
//
// The property under test: the trace renders as ONE interleaved stream in the
// order things happened (message → tool → tool → message), not three disjoint
// lists; and the owner/skill facets make selecting a user's sessions a click,
// not a search incantation.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnalyticsPage from "../page";
import { buildTimeline } from "../timeline";

const auth: { user: unknown; loading: boolean } = { user: { email: "a@x.com" }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/chat/SignInRequired", () => ({ SignInRequired: () => <div>sign in</div> }));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";

const SESSIONS = {
  sessions: [
    {
      session_id: "sess-1",
      skill_id: "one-ppa-expert",
      skill_label: "Contract Expert",
      owner_uid: "u1",
      owner_email: "dv@acme-energy.example",
      owner_name: "Dana Vega",
      title: "DK1 electricity prices",
      turn_count: 2,
      document_count: 2,
      first_message_at: "2026-08-09T12:38:00+00:00",
      last_message_at: "2026-08-09T12:40:00+00:00",
      archived: false,
      transcript_lost: false,
    },
    {
      session_id: "sess-2",
      skill_id: "web-researcher",
      skill_label: "Web Researcher",
      owner_uid: "u2",
      owner_email: "owner@yourcompany.com",
      owner_name: "Mark",
      title: "Market scan",
      turn_count: 1,
      document_count: 0,
      first_message_at: "2026-08-08T09:00:00+00:00",
      last_message_at: "2026-08-08T09:05:00+00:00",
      archived: false,
      transcript_lost: true,
    },
  ],
  owners: [
    { uid: "u1", email: "dv@acme-energy.example", name: "Dana Vega", sessions: 5, last_active: "2026-08-09" },
    { uid: "u2", email: "owner@yourcompany.com", name: "Mark", sessions: 3, last_active: "2026-08-08" },
  ],
  skills: [
    { id: "one-ppa-expert", label: "Contract Expert", sessions: 6 },
    { id: "web-researcher", label: "Web Researcher", sessions: 2 },
  ],
  hidden_empty: 214,
};

const TRACE = {
  session_id: "sess-1",
  skill_id: "one-ppa-expert",
  skill_label: "Contract Expert",
  owner_uid: "u1",
  owner_email: "dv@acme-energy.example",
  owner_name: "Dana Vega",
  title: "DK1 electricity prices",
  turn_count: 2,
  first_message_at: "2026-08-09T12:38:00+00:00",
  last_message_at: "2026-08-09T12:40:00+00:00",
  documents: [{ id: "doc-a", name: "demo-leap.pdf" }],
  session_start_ts: 1000,
  event_count: 6,
  transcript_available: true,
  messages: [
    { role: "user", content: "compare the PPAs", timestamp: 1000 },
    { role: "assistant", content: "Here is the comparison.", timestamp: 4000, agent_label: "Contract Expert" },
  ],
  tools: [
    { id: "t1", name: "list_documents", status: "success", ts: 2000, argsJson: null, resultContent: null },
    { id: "t2", name: "compare_ppa_contracts", status: "success", ts: 3000, argsJson: '{"left_doc_id": "a"}' },
  ],
  delegations: [],
};

function mockRoutes(overrides?: { sessions?: unknown; trace?: unknown }) {
  vi.mocked(fetchWithAuth).mockImplementation(async (url: string | URL | Request) => {
    const u = String(url);
    const body = u.includes("/sessions/") ? (overrides?.trace ?? TRACE) : (overrides?.sessions ?? SESSIONS);
    return { ok: true, status: 200, json: async () => body } as unknown as Response;
  });
}

describe("buildTimeline", () => {
  it("interleaves messages, tools and delegations in chronological order", () => {
    const items = buildTimeline({
      messages: TRACE.messages as never,
      tools: TRACE.tools as never,
      delegations: [{ id: "d1", target: "x", targetDisplay: "X", mode: "auto", ts: 2500 }],
    });
    expect(items.map((i) => i.kind)).toEqual(["message", "tool", "delegation", "tool", "message"]);
    expect(items.map((i) => i.ts)).toEqual([1000, 2000, 2500, 3000, 4000]);
  });
});

describe("AnalyticsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { email: "a@x.com" };
    auth.loading = false;
  });

  it("renders session rows with owner, skill label, docs and transcript-lost badge", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    expect(screen.getAllByText(/Dana Vega/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Contract Expert").length).toBeGreaterThan(0);
    expect(screen.getByText(/2 docs/)).toBeTruthy();
    expect(screen.getByText(/transcript lost/i)).toBeTruthy();
  });

  it("says how many never-used sessions were hidden (not a silent shrink)", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText(/214 never-used sessions hidden/)).toBeTruthy());
  });

  it("offers owner and skill facet selectors with counts, and refetches on selection", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    const ownerSelect = screen.getByLabelText("Filter by user") as HTMLSelectElement;
    expect(within(ownerSelect).getByText("Dana Vega (5)")).toBeTruthy();
    fireEvent.change(ownerSelect, { target: { value: "u1" } });
    await waitFor(() => {
      const calls = vi.mocked(fetchWithAuth).mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.includes("owner_uid=u1"))).toBe(true);
    });
    const skillSelect = screen.getByLabelText("Filter by skill") as HTMLSelectElement;
    expect(within(skillSelect).getByText("Web Researcher (2)")).toBeTruthy();
  });

  it("renders the trace as one chronological timeline (message → tools → message)", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    fireEvent.click(screen.getByText("DK1 electricity prices"));
    await waitFor(() => expect(screen.getByText("compare the PPAs")).toBeTruthy());

    const list = screen.getByRole("tab", { name: /Timeline/ }).closest("div")!
      .parentElement!.querySelector("ul")!;
    const texts = Array.from(list.querySelectorAll("li")).map((li) => li.textContent || "");
    const order = ["compare the PPAs", "list_documents", "compare_ppa_contracts", "Here is the comparison."];
    const positions = order.map((t) => texts.findIndex((x) => x.includes(t)));
    expect(positions.every((p) => p >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions); // strictly in order
  });

  it("shows trace metadata (docs, events) and a Raw JSON view", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    fireEvent.click(screen.getByText("DK1 electricity prices"));
    await waitFor(() => expect(screen.getByText("demo-leap.pdf")).toBeTruthy());
    expect(screen.getByText("Events")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Raw JSON" }));
    expect(screen.getByText(/Full trace payload/i)).toBeTruthy();
    expect(screen.getByText(/"session_id": "sess-1"/)).toBeTruthy();
  });

  it("expands a timeline tool row to show what it was called with", async () => {
    mockRoutes();
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    fireEvent.click(screen.getByText("DK1 electricity prices"));
    await waitFor(() => expect(screen.getByText("compare_ppa_contracts")).toBeTruthy());
    fireEvent.click(screen.getByText("compare_ppa_contracts"));
    await waitFor(() => expect(screen.getByText("Called with")).toBeTruthy());
    expect(screen.getByText("left_doc_id")).toBeTruthy();
  });

  it("keeps the transcript-unavailable notice (never-silent)", async () => {
    mockRoutes({ trace: { ...TRACE, transcript_available: false, messages: [], tools: [], delegations: [] } });
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText("DK1 electricity prices")).toBeTruthy());
    fireEvent.click(screen.getByText("DK1 electricity prices"));
    await waitFor(() => expect(screen.getAllByText(/Transcript unavailable/).length).toBeGreaterThan(0));
  });

  it("surfaces a list load failure (never-silent)", async () => {
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("offline"));
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByText(/Could not load sessions/i)).toBeTruthy());
  });
});
