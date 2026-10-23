import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), fetch: vi.fn(),
}));
vi.mock("@/lib/audioCapture", () => ({
  isAudioCaptureSupported: () => true,
  AudioRecorder: class {
    start = mocks.start;
    stop = mocks.stop;
    cancel = mocks.cancel;
    recording = true;
    getLevel = () => 0;
  },
}));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: mocks.fetch }));
import { VoiceComposerControls } from "../VoiceComposerControls";

const props = () => ({ skillId: "skill", voiceInputEnabled: true,
  onTranscript: vi.fn(), onNotice: vi.fn(), onBusyChange: vi.fn() });

beforeEach(() => {
  vi.resetAllMocks();
  mocks.start.mockResolvedValue(undefined);
  mocks.stop.mockResolvedValue({ blob: new Blob(["wav"], { type: "audio/wav" }) });
});

describe("dictation", () => {
  it("records, transcribes into a draft callback, and leaves sending to the user", async () => {
    mocks.fetch.mockResolvedValue({ ok: true, json: async () => ({ text: " dictated text " }) });
    const p = props();
    render(<VoiceComposerControls {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Talk to type" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop dictation" }));
    await waitFor(() => expect(p.onTranscript).toHaveBeenCalledWith("dictated text"));
    expect(p.onBusyChange).toHaveBeenLastCalledWith(false);
    expect(mocks.fetch.mock.calls[0][1].body.get("skillId")).toBe("skill");
  });

  it("releases the mic on navigation and ignores a late transcription", async () => {
    let complete!: (value: unknown) => void;
    mocks.fetch.mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    const p = props();
    const { unmount } = render(<VoiceComposerControls {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Talk to type" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop dictation" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalled());
    const signal = mocks.fetch.mock.calls[0][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    expect(mocks.cancel).toHaveBeenCalled();
    complete({ ok: true, json: async () => ({ text: "late result" }) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(p.onTranscript).not.toHaveBeenCalled();
  });

  it("lets the user cancel transcription without adding text", async () => {
    mocks.fetch.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new Error("aborted")));
    }));
    const p = props();
    render(<VoiceComposerControls {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Talk to type" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop dictation" }));
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Cancel dictation" }));
    expect(p.onBusyChange).toHaveBeenLastCalledWith(false);
    expect(p.onTranscript).not.toHaveBeenCalled();
  });
});
