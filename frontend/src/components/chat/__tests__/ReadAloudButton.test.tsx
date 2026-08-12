import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { ReadAloudButton } from "../ReadAloudButton";

const mockFetch = fetchWithAuth as unknown as ReturnType<typeof vi.fn>;

// jsdom ships neither URL.createObjectURL nor a usable HTMLAudioElement.play.
// Stub both so the GCP path can run to completion in a test.
const playSpy = vi.fn().mockResolvedValue(undefined);
const pauseSpy = vi.fn();

beforeEach(() => {
  mockFetch.mockReset();
  playSpy.mockClear();
  pauseSpy.mockClear();

  (globalThis as { URL: typeof URL }).URL.createObjectURL = vi.fn(() => "blob:mock-audio");
  (globalThis as { URL: typeof URL }).URL.revokeObjectURL = vi.fn();

  // Minimal Audio stub — the component only calls play/pause + the on* hooks.
  vi.stubGlobal(
    "Audio",
    class {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      play = playSpy;
      pause = pauseSpy;
      constructor(public src?: string) {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function audioResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "audio/mpeg", "x-voice-provider": "gcp_wavenet" }),
    blob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "audio/mpeg" }),
  } as unknown as Response;
}

describe("ReadAloudButton", () => {
  it("renders a play control", () => {
    render(<ReadAloudButton text="Hola" provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    expect(screen.getByRole("button", { name: /read aloud/i })).toBeInTheDocument();
  });

  it("does NOT auto-play on mount (no fetch, no audio)", () => {
    render(<ReadAloudButton text="Hola" provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(playSpy).not.toHaveBeenCalled();
  });

  it("clicking calls fetchWithAuth to the synthesize proxy route", async () => {
    mockFetch.mockResolvedValue(audioResponse());
    render(
      <ReadAloudButton text="Hola, soy Aitana" provider="gcp_wavenet" voice="es-ES-Wavenet-C" skillId="s1" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /read aloud/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/voice/tts/synthesize");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.text).toContain("Aitana");
    expect(body.voice).toBe("es-ES-Wavenet-C");
    expect(body.skillId).toBe("s1");
    await waitFor(() => expect(playSpy).toHaveBeenCalled());
  });

  it("barge-in: a voice.cancel event stops in-flight audio", async () => {
    mockFetch.mockResolvedValue(audioResponse());
    render(<ReadAloudButton text="Hola" provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    fireEvent.click(screen.getByRole("button", { name: /read aloud/i }));
    await waitFor(() => expect(playSpy).toHaveBeenCalled());

    // Simulate another button starting: dispatch the shared cancel event.
    act(() => {
      window.dispatchEvent(new CustomEvent("aitana:voice.cancel"));
    });
    expect(pauseSpy).toHaveBeenCalled();
    // After a stop, the control reverts to "Read aloud" (not "Stop").
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /read aloud/i })).toBeInTheDocument(),
    );
  });

  it("clicking while speaking stops playback (toggle)", async () => {
    mockFetch.mockResolvedValue(audioResponse());
    render(<ReadAloudButton text="Hola" provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    const btn = screen.getByRole("button");
    fireEvent.click(btn);
    await waitFor(() => expect(playSpy).toHaveBeenCalled());
    // Second click while speaking -> stop.
    fireEvent.click(screen.getByRole("button", { name: /stop reading aloud/i }));
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("shows stop button immediately on click (before audio plays)", async () => {
    // setIsSpeaking(true) now fires before fetch completes, not after play().
    mockFetch.mockResolvedValue(audioResponse());
    render(<ReadAloudButton text="Hola" provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    fireEvent.click(screen.getByRole("button", { name: /read aloud/i }));
    // The button should be "Stop reading aloud" before we even wait for play.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /stop reading aloud/i })).toBeInTheDocument(),
    );
  });

  it("long text: splits into chunks and prefetches next while playing current", async () => {
    // A single sentence repeated until the text exceeds CHUNK_MAX_CHARS (600).
    // splitAtSentences will cut at a sentence boundary, producing 2 chunks.
    // The pipeline starts prefetching chunk 1 when it begins playing chunk 0,
    // so both fetchWithAuth calls happen without needing onended to fire.
    const sentence = "This is a test sentence for the read-aloud chunking feature. ";
    const text = sentence.repeat(12); // 732 chars > 600

    mockFetch.mockResolvedValue(audioResponse());
    render(<ReadAloudButton text={text} provider="gcp_wavenet" voice="es-ES-Wavenet-C" />);
    fireEvent.click(screen.getByRole("button", { name: /read aloud/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));

    const body0 = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    const body1 = JSON.parse(mockFetch.mock.calls[1][1].body as string);
    // Both chunks go to the same endpoint.
    expect(body0.text).toBeTruthy();
    expect(body1.text).toBeTruthy();
    // Chunks are different slices of the original.
    expect(body0.text).not.toEqual(body1.text);
    // Together they cover the whole text (no content lost).
    const combined = body0.text + " " + body1.text;
    expect(combined.replace(/\s+/g, " ").trim()).toEqual(text.trim().replace(/\s+/g, " "));
  });
});
