/**
 * An A2UI image on an authenticated backend route.
 *
 * Reported from test on 2026-09-04: the browser pane rendered, the activity
 * list rendered, and the screenshot was a broken image. The emitted HTML was
 *
 *   <img src="/api/browser/artefacts/browser_screenshot_….png">
 *
 * which fails twice — the path resolves against the FRONTEND origin (404), and
 * even corrected an <img> sends no Authorization header, which the proxy passes
 * through rather than injecting (401).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

import { findAuthImages, resolveAuthImages } from "@/lib/a2uiAuthImages";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/lib/logger", () => ({ logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { fetchWithAuth } from "@/lib/apiClient";

const ARTEFACT = "/api/browser/artefacts/browser_screenshot_e-abc_deadbeef.png";

function imageMessage(url: string) {
  return {
    version: "v0.9",
    updateComponents: {
      surfaceId: "browser",
      components: [
        { id: "t-1", component: "Text", text: "Example Domain" },
        { id: "img-1", component: "Image", url, description: "Screenshot" },
      ],
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.URL.createObjectURL = vi.fn(() => "blob:fake-object-url");
});

describe("findAuthImages", () => {
  it("finds an image on a backend path", () => {
    expect(findAuthImages([imageMessage(ARTEFACT)])).toHaveLength(1);
  });

  it("ignores an image that is already proxied", () => {
    expect(findAuthImages([imageMessage(`/api/proxy${ARTEFACT}`)])).toHaveLength(0);
  });

  it("ignores an absolute or blob url", () => {
    expect(findAuthImages([imageMessage("https://example.com/a.png")])).toHaveLength(0);
    expect(findAuthImages([imageMessage("blob:already-resolved")])).toHaveLength(0);
  });

  it("ignores non-image components", () => {
    const msg = {
      version: "v0.9",
      updateComponents: { surfaceId: "browser", components: [{ id: "t", component: "Text", text: "x" }] },
    };
    expect(findAuthImages([msg])).toHaveLength(0);
  });
});

describe("resolveAuthImages", () => {
  it("fetches through the PROXY path, not the bare backend path", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: true, blob: async () => new Blob() } as unknown as Response);

    await resolveAuthImages("browser", [imageMessage(ARTEFACT)]);

    expect(fetchWithAuth).toHaveBeenCalledWith(`/api/proxy${ARTEFACT}`);
  });

  it("patches the component with a blob url, keeping its id", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: true, blob: async () => new Blob() } as unknown as Response);

    const { patches } = await resolveAuthImages("browser", [imageMessage(ARTEFACT)]);

    expect(patches).toHaveLength(1);
    const comp = (patches[0].updateComponents as { components: { id: string; url: string }[] }).components[0];
    expect(comp.id).toBe("img-1");
    expect(comp.url).toBe("blob:fake-object-url");
  });

  it("a failed fetch leaves the surface alone rather than taking the pane down", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ ok: false, status: 401 } as unknown as Response);

    const { patches } = await resolveAuthImages("browser", [imageMessage(ARTEFACT)]);

    expect(patches).toHaveLength(0);
  });

  it("a thrown fetch is caught, not propagated", async () => {
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("network down"));

    await expect(resolveAuthImages("browser", [imageMessage(ARTEFACT)])).resolves.toEqual({
      patches: [],
      revoke: [],
    });
  });
});
