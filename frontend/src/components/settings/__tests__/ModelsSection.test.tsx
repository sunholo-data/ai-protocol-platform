// Settings → Models: the opt-in for specific models, shown only where the
// deployment offers some.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let menu: unknown = null;
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/apiClient")>();
  return {
    ...mod,
    fetchWithAuth: vi.fn(async () => ({ ok: menu !== null, status: menu ? 200 : 404, json: async () => menu }) as unknown as Response),
  };
});

import { ModelsSection } from "@/components/settings/ModelsSection";
import { resetModelMenuCache } from "@/lib/modelChoice";

beforeEach(() => {
  resetModelMenuCache();
  const store: Record<string, string> = {};
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = v;
    },
    removeItem: (k: string) => {
      delete store[k];
    },
  });
});

const OFFERED = {
  offered: "all",
  levels: {},
  models: [{ id: "gemini-3-8-flash", label: "Gemini 3.8 Flash" }],
  labels: {},
  resolved: {},
};

describe("ModelsSection", () => {
  it("is off by default and turning it on sticks", async () => {
    menu = OFFERED;
    render(<ModelsSection />);
    const sw = await screen.findByRole("switch", { name: "Choose a specific model" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    expect(window.localStorage.getItem("platform.models.specific")).toBe("1");
  });

  it("is not there when the deployment offers no specific models", async () => {
    menu = { ...OFFERED, offered: "tiers", models: [] };
    const { container } = render(<ModelsSection />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
