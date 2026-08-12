// A2UIRenderer — v0.9 inline renderer tests
//
// The renderer owns a per-bubble MessageProcessor + SurfaceModel. These
// tests cover the contract:
//   - Valid v0.9 message array → <A2uiSurface> renders with the resulting
//     SurfaceModel.
//   - First message isn't createSurface AND no prior surface → auto-create
//     with basicCatalog (dev warning).
//   - Non-array payload → debug fallback rendered, no crash.
//   - SDK throws (e.g. duplicate createSurface) → fallback rendered.

import { render, screen, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ─── v0.9 SDK doubles (inlined inside vi.mock factory — TDZ-safe) ──────────

vi.mock("@a2ui/web_core/v0_9", () => {
  class FakeProcessor {
    private surfaces = new Map<string, {
      id: string;
      catalog: { id: string };
      dispose: () => void;
    }>();
    public readonly model = {
      getSurface: (id: string) => this.surfaces.get(id),
      onAction: { subscribe: () => ({ unsubscribe: () => {} }) },
    };

    processMessages(messages: Array<Record<string, unknown>>) {
      for (const msg of messages) {
        if (msg.createSurface) {
          const p = msg.createSurface as { surfaceId: string; catalogId: string };
          if (this.surfaces.has(p.surfaceId))
            throw new Error(`Surface ${p.surfaceId} already exists.`);
          this.surfaces.set(p.surfaceId, {
            id: p.surfaceId,
            catalog: { id: p.catalogId },
            dispose: () => {},
          });
        } else if (msg.deleteSurface) {
          const p = msg.deleteSurface as { surfaceId: string };
          this.surfaces.delete(p.surfaceId);
        }
      }
    }
  }
  return { MessageProcessor: FakeProcessor };
});

vi.mock("@a2ui/react/v0_9", () => ({
  basicCatalog: { id: "https://a2ui.org/specification/v0_9/basic_catalog.json" },
  A2uiSurface: ({ surface }: { surface: { id: string } }) => (
    <div data-testid="a2ui-surface" data-surface-id={surface.id} />
  ),
}));

vi.mock("@a2ui/react/styles", () => ({
  injectStyles: vi.fn(),
}));

// ─── Import after mocks ─────────────────────────────────────────────────────

import { A2UIRenderer } from "@/components/protocols/A2UIRenderer";

const CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json";

function createMsg(surfaceId: string) {
  return {
    version: "v0.9",
    createSurface: { surfaceId, catalogId: CATALOG_ID },
  };
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("A2UIRenderer (v0.9)", () => {
  it("renders <A2uiSurface> when given a valid v0.9 message array", async () => {
    await act(async () => {
      render(<A2UIRenderer messages={[createMsg("inline-1")]} />);
    });
    const surface = await screen.findByTestId("a2ui-surface");
    expect(surface.getAttribute("data-surface-id")).toBe("inline-1");
  });

  it("auto-creates the surface when the first message is not createSurface", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const messages = [
      {
        version: "v0.9",
        updateDataModel: { surfaceId: "implicit", value: { x: 1 } },
      },
    ];
    await act(async () => {
      render(
        <A2UIRenderer messages={messages} fallbackSurfaceId="implicit" />,
      );
    });
    const surface = await screen.findByTestId("a2ui-surface");
    expect(surface.getAttribute("data-surface-id")).toBe("implicit");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("falls back to <pre> when messages is not an array", async () => {
    await act(async () => {
      render(<A2UIRenderer messages={"not an array" as unknown} />);
    });
    expect(screen.getByTestId("a2ui-fallback").textContent).toContain(
      "not a v0.9 message array",
    );
  });

  it("falls back when processMessages throws (e.g. duplicate createSurface)", async () => {
    const messages = [createMsg("dup"), createMsg("dup")];
    await act(async () => {
      render(<A2UIRenderer messages={messages} />);
    });
    const fallback = screen.getByTestId("a2ui-fallback");
    expect(fallback.textContent).toContain("processMessages failed");
  });
});
