import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { useVoiceConfig, _resetVoiceConfigCacheForTests } from "../useVoiceConfig";

const fetchMock = fetchWithAuth as unknown as ReturnType<typeof vi.fn>;

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const GCP = { tts: { provider: "gcp_chirp3hd", voice: "es-ES-Chirp3-HD-Aoede", language: "es" } };

beforeEach(() => {
  _resetVoiceConfigCacheForTests();
  fetchMock.mockReset();
});

describe("useVoiceConfig", () => {
  it("resolves the skill's config and reports loading until then", async () => {
    fetchMock.mockResolvedValueOnce(ok(GCP));
    const { result } = renderHook(() => useVoiceConfig("skill-a"));
    expect(result.current.loading).toBe(true);
    expect(result.current.tts.provider).toBe("browser");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.tts.provider).toBe("gcp_chirp3hd");
    expect(result.current.tts.enabled).toBe(true); // merged over defaults
    expect(fetchMock.mock.calls[0][0]).toBe("/api/proxy/api/voice/config?skill_id=skill-a");
  });

  it("ONE request per skill however many bubbles mount at once", async () => {
    // Prod 2026-09-14: 40 MessageBubbles → 40 parallel requests on mount,
    // because nothing was cached until the first one returned.
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (resolve = r)));
    const hooks = Array.from({ length: 12 }, () => renderHook(() => useVoiceConfig("skill-a")));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve(ok(GCP));
    });
    await waitFor(() => hooks.forEach((h) => expect(h.result.current.tts.provider).toBe("gcp_chirp3hd")));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a window focus refreshes each live key ONCE, not once per bubble", async () => {
    fetchMock.mockResolvedValue(ok(GCP));
    const hooks = Array.from({ length: 5 }, () => renderHook(() => useVoiceConfig("skill-a")));
    await waitFor(() => hooks.forEach((h) => expect(h.result.current.loading).toBe(false)));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    hooks.forEach((h) => h.unmount());
    // No subscribers left → a focus fetches nothing.
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("falls back to the browser provider on error and stops reporting loading", async () => {
    fetchMock.mockResolvedValueOnce(new Response("nope", { status: 500 }));
    const { result } = renderHook(() => useVoiceConfig(null));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.tts.provider).toBe("browser");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/proxy/api/voice/config");
  });
});
