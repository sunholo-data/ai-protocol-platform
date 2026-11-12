// The + menu's Model group on a phone (the bottom sheet): every choice is a
// large radio row, the current pick is checked, and a skill that keeps its
// model says so instead of offering choices.
import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...mod, fetchWithAuth: vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as unknown as Response) };
});

import { ComposerAddMenu, type ModelPicker } from "@/components/chat/ComposerAddMenu";

function phone() {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: q.includes("max-width"),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function renderMenu(model: ModelPicker) {
  render(
    <ComposerAddMenu
      onUpload={() => {}}
      recentDocuments={[]}
      onOpenDocument={() => {}}
      onBrowseFiles={() => {}}
      readAloud={false}
      model={model}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Add files and more" }));
}

const OPTIONS = {
  show: true,
  locked: null,
  levels: [
    { value: "auto", title: "Auto", detail: "Writer uses Gemini 3.8 Flash" },
    { value: "fast", title: "Fast", detail: "Gemini 3.5 Flash-Lite · quicker, for simple questions" },
  ],
  models: [{ value: "model:gemini-3-8-flash", title: "Gemini 3.8 Flash", detail: "balanced" }],
};

beforeEach(() => phone());

describe("ComposerAddMenu — Model on a phone", () => {
  it("lists the choices with the current one checked, and picking one reports it", () => {
    const onChange = vi.fn();
    renderMenu({ options: OPTIONS, value: "auto", valueLabel: "Auto", onChange });
    const group = screen.getByRole("radiogroup", { name: "Model" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /^Auto/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /^Gemini 3\.8 Flash/ })).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByRole("radio", { name: /^Fast/ }));
    expect(onChange).toHaveBeenCalledWith("fast");
  });

  it("a skill that keeps its model says so", () => {
    renderMenu({
      options: { show: true, locked: "GPT always uses GPT-5.6 Luna.", levels: [], models: [] },
      value: "auto",
      valueLabel: "Auto",
      onChange: () => {},
    });
    expect(screen.getByText("GPT always uses GPT-5.6 Luna.")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });

  it("no Model group when there is nothing to show", () => {
    renderMenu({ options: { show: false, locked: null, levels: [], models: [] }, value: "auto", valueLabel: "Auto", onChange: () => {} });
    expect(screen.queryByText("Model")).toBeNull();
  });
});
