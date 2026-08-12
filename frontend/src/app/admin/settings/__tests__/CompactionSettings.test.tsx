// Compaction tuning section (v6.23.0, 1b + 1e).
//
// The properties worth pinning are the ones that protect a live conversation:
// an empty field CLEARS a lever (rather than writing 0), a prompt missing the
// placeholder cannot be saved (it would raise inside a user's turn), and the
// retention floor — the surprise that produced a wrong test result once — is
// stated on screen.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompactionSettingsSection } from "../CompactionSettings";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args) }));

function bodyOf(call: unknown[]): Record<string, unknown> {
  const init = call[1] as { body: string };
  return JSON.parse(init.body).compaction;
}

describe("CompactionSettingsSection", () => {
  beforeEach(() => {
    fetchWithAuth.mockReset();
    fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ updatedBy: "admin", updatedAt: 1 }) });
  });

  it("sends nulls for empty fields so cleared levers restore shipped defaults", async () => {
    render(<CompactionSettingsSection initial={{}} />);
    fireEvent.click(screen.getByRole("button", { name: /save compaction settings/i }));

    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    const body = bodyOf(fetchWithAuth.mock.calls[0]);
    expect(body.tokenThreshold).toBeNull();
    expect(body.eventRetentionSize).toBeNull();
    expect(body.summarizerPrompt).toBeNull();
    expect(body.summarizerModel).toBeNull();
  });

  it("round-trips stored values and converts idle seconds to minutes", async () => {
    render(<CompactionSettingsSection initial={{ secondPassEnabled: true, secondPassIdleSeconds: 1800 }} />);
    expect(screen.getByLabelText("Idle wait (minutes)")).toHaveValue("30");

    fireEvent.click(screen.getByRole("button", { name: /save compaction settings/i }));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(bodyOf(fetchWithAuth.mock.calls[0]).secondPassIdleSeconds).toBe(1800);
  });

  it("blocks a summariser prompt without the placeholder", async () => {
    render(<CompactionSettingsSection initial={{}} />);
    fireEvent.change(screen.getByLabelText("Summariser prompt"), { target: { value: "summarise it nicely" } });

    expect(await screen.findByText(/must contain \{conversation_history\}/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save compaction settings/i })).toBeDisabled();

    // ...and accepts it once the placeholder is present.
    fireEvent.change(screen.getByLabelText("Summariser prompt"), {
      target: { value: "summarise {conversation_history}" },
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /save compaction settings/i })).not.toBeDisabled(),
    );
  });

  it("blocks a non-numeric threshold rather than posting it", async () => {
    render(<CompactionSettingsSection initial={{}} />);
    fireEvent.change(screen.getByLabelText("Token threshold"), { target: { value: "lots" } });

    expect(screen.getByRole("button", { name: /save compaction settings/i })).toBeDisabled();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("explains the retention floor and warns that quality changes are invisible", () => {
    render(<CompactionSettingsSection initial={{}} />);
    expect(screen.getByText(/cannot fire until a conversation exceeds/i)).toBeInTheDocument();
    expect(screen.getByText(/change answer quality silently/i)).toBeInTheDocument();
  });

  it("surfaces a server rejection instead of claiming success", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, json: async () => ({ detail: "summarizer_prompt must contain X" }) });
    render(<CompactionSettingsSection initial={{}} />);
    fireEvent.click(screen.getByRole("button", { name: /save compaction settings/i }));

    expect(await screen.findByText(/summarizer_prompt must contain X/)).toBeInTheDocument();
  });
});

describe("CompactionSettingsSection — shipped prompt pre-fill", () => {
  const SHIPPED = "Preserve every figure. {conversation_history}";

  beforeEach(() => {
    fetchWithAuth.mockReset();
    fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ updatedBy: "admin", updatedAt: 1 }) });
  });

  it("opens on the shipped prompt so it can be edited in place", () => {
    render(<CompactionSettingsSection initial={{}} defaultPrompt={SHIPPED} />);
    expect(screen.getByLabelText("Summariser prompt")).toHaveValue(SHIPPED);
  });

  it("saves null when the prompt is untouched, so it keeps tracking the shipped one", async () => {
    render(<CompactionSettingsSection initial={{}} defaultPrompt={SHIPPED} />);
    fireEvent.click(screen.getByRole("button", { name: /save compaction settings/i }));

    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(bodyOf(fetchWithAuth.mock.calls[0]).summarizerPrompt).toBeNull();
  });

  it("saves the text once it genuinely differs", async () => {
    render(<CompactionSettingsSection initial={{}} defaultPrompt={SHIPPED} />);
    fireEvent.change(screen.getByLabelText("Summariser prompt"), {
      target: { value: "Keep clause numbers only. {conversation_history}" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save compaction settings/i }));

    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(bodyOf(fetchWithAuth.mock.calls[0]).summarizerPrompt).toBe(
      "Keep clause numbers only. {conversation_history}",
    );
  });

  it("offers a reset that restores the shipped text", async () => {
    render(
      <CompactionSettingsSection
        initial={{ summarizerPrompt: "old override {conversation_history}" }}
        defaultPrompt={SHIPPED}
      />,
    );
    expect(screen.getByLabelText("Summariser prompt")).toHaveValue("old override {conversation_history}");

    fireEvent.click(screen.getByRole("button", { name: /reset to shipped prompt/i }));
    expect(screen.getByLabelText("Summariser prompt")).toHaveValue(SHIPPED);
    expect(screen.queryByRole("button", { name: /reset to shipped prompt/i })).not.toBeInTheDocument();
  });
});
