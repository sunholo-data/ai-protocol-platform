import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
  new Response("", { status: 200 }),
);
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (url: string, init?: RequestInit) => fetchMock(url, init),
}));

import { ArtefactRefused } from "../ArtefactRefused";

const baseProps = {
  decision: {
    action: "block" as const,
    message: "Cohort PHYS-7K2N is over its monthly budget.",
    reasonCode: "OVER_BUDGET",
  },
  toolName: "physics_sim_builder",
  serverId: "demo-mcp-server",
  invocationId: "inv-abc",
};

describe("ArtefactRefused", () => {
  beforeEach(() => {
    fetchMock.mockClear();
  });

  it("renders the message verbatim", () => {
    render(<ArtefactRefused {...baseProps} />);
    expect(screen.getByText(baseProps.decision.message)).toBeInTheDocument();
  });

  it("renders the reason code in a monospace chip", () => {
    render(<ArtefactRefused {...baseProps} />);
    expect(screen.getByTestId("artefact-refused-reason")).toHaveTextContent("OVER_BUDGET");
  });

  it("uses role='alert' + aria-live='assertive' for screen-reader announcement", () => {
    render(<ArtefactRefused {...baseProps} />);
    const root = screen.getByTestId("artefact-refused");
    expect(root).toHaveAttribute("role", "alert");
    expect(root).toHaveAttribute("aria-live", "assertive");
  });

  it("renders the appeal link when appealUrl is present", () => {
    render(
      <ArtefactRefused
        {...baseProps}
        decision={{ ...baseProps.decision, appealUrl: "https://example.com/appeal" }}
      />,
    );
    const appeal = screen.getByTestId("artefact-refused-appeal");
    expect(appeal).toHaveAttribute("href", "https://example.com/appeal");
    expect(appeal).toHaveAttribute("target", "_blank");
    expect(appeal).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("omits the appeal link when appealUrl is absent", () => {
    render(<ArtefactRefused {...baseProps} />);
    expect(screen.queryByTestId("artefact-refused-appeal")).not.toBeInTheDocument();
  });

  it("on mount, fires the audit POST when sessionId is set", async () => {
    render(<ArtefactRefused {...baseProps} sessionId="sess-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.at(-1)!;
    const [url, init] = call;
    expect(url).toBe("/api/proxy/api/sessions/sess-1/artefact-blocked");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      tool_name: "physics_sim_builder",
      server_id: "demo-mcp-server",
      reason_code: "OVER_BUDGET",
      invocation_id: "inv-abc",
    });
  });

  it("does NOT fire the audit POST when sessionId is null (pre-first-turn render)", async () => {
    render(<ArtefactRefused {...baseProps} sessionId={null} />);
    // Give effects a tick.
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("only fires the audit POST once even under Strict-Mode double-mount", async () => {
    const { rerender } = render(<ArtefactRefused {...baseProps} sessionId="sess-1" />);
    rerender(<ArtefactRefused {...baseProps} sessionId="sess-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});
