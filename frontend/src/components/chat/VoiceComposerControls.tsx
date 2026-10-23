"use client";

/** AIPLA's talk-to-type: stop recording, transcribe, review the draft, send. */
import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square, X } from "lucide-react";
import { fetchWithAuth } from "@/lib/apiClient";
import { AudioRecorder, isAudioCaptureSupported } from "@/lib/audioCapture";
import { cancelVoicePlayback } from "@/hooks/useAutoReadAloud";
import { RecordingLevelMeter } from "./RecordingLevelMeter";

interface Props {
  skillId: string;
  lang?: string;
  voiceInputEnabled: boolean;
  disabled?: boolean;
  maxDurationSeconds?: number;
  onTranscript: (text: string) => void;
  onBusyChange: (busy: boolean) => void;
  onNotice: (notice: string | null) => void;
}

export function VoiceComposerControls({ skillId, lang = "auto", voiceInputEnabled,
  disabled, maxDurationSeconds = 60, onTranscript, onBusyChange, onNotice }: Props) {
  const recorder = useRef<AudioRecorder | null>(null);
  const request = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  const [supported, setSupported] = useState(false);
  const [mode, setMode] = useState<"idle" | "starting" | "recording" | "transcribing">("idle");

  useEffect(() => {
    alive.current = true;
    setSupported(isAudioCaptureSupported());
    return () => {
      alive.current = false;
      recorder.current?.cancel();
      request.current?.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    if (!voiceInputEnabled || disabled) {
      recorder.current?.cancel();
      recorder.current = null;
      request.current?.abort();
      request.current = null;
      if (timer.current) clearTimeout(timer.current);
      setMode("idle");
      onBusyChange(false);
    }
  }, [voiceInputEnabled, disabled, onBusyChange]);

  function finish() { setMode("idle"); onBusyChange(false); }

  function cancel() {
    if (timer.current) clearTimeout(timer.current);
    recorder.current?.cancel();
    recorder.current = null;
    request.current?.abort();
    finish();
    onNotice(null);
  }

  async function stopAndTranscribe() {
    if (timer.current) clearTimeout(timer.current);
    setMode("transcribing");
    const controller = new AbortController();
    request.current = controller;
    try {
      const { blob } = await recorder.current!.stop();
      const body = new FormData();
      body.append("audio", blob, "dictation.wav");
      body.append("lang", lang);
      body.append("skillId", skillId);
      const response = await fetchWithAuth("/api/proxy/api/voice/stt/transcribe", {
        method: "POST", body, signal: controller.signal,
      });
      if (!response.ok) throw new Error("Transcription failed");
      const data = await response.json() as { text?: string };
      if (!alive.current || controller.signal.aborted) return;
      if (data.text?.trim()) {
        onTranscript(data.text.trim());
        onNotice("Review your message, then send.");
      } else onNotice("No speech detected. Try again or type your message.");
    } catch {
      if (alive.current && !controller.signal.aborted)
        onNotice("Could not transcribe. Try again or type your message.");
    } finally {
      if (alive.current && !controller.signal.aborted) finish();
    }
  }

  async function begin() {
    setMode("starting");
    onBusyChange(true);
    onNotice(null);
    cancelVoicePlayback();
    const capture = new AudioRecorder();
    recorder.current = capture;
    try {
      await capture.start();
      if (!alive.current || !capture.recording) return;
      setMode("recording");
      onNotice(`Recording — stop when finished (up to ${maxDurationSeconds} seconds).`);
      timer.current = setTimeout(() => void stopAndTranscribe(), maxDurationSeconds * 1000);
    } catch {
      if (alive.current && recorder.current === capture) {
        onNotice("Microphone unavailable. Check permission or type your message.");
        finish();
      }
    }
  }

  if (!voiceInputEnabled || !supported) return null;
  const style = "inline-flex min-h-9 min-w-9 items-center justify-center rounded-md border px-2 text-muted-foreground hover:bg-accent disabled:opacity-40";
  return (
    <div className="inline-flex items-center gap-2">
      {mode === "recording" && <RecordingLevelMeter getLevel={() => recorder.current?.getLevel() ?? 0} className="text-red-600" />}
      <button type="button" className={style}
        disabled={mode === "starting" || mode === "transcribing" || (mode === "idle" && disabled)}
        aria-label={mode === "recording" ? "Stop dictation" : mode === "transcribing" ? "Transcribing" : "Talk to type"}
        title={mode === "recording" ? "Stop dictation" : "Talk to type"}
        onClick={() => void (mode === "recording" ? stopAndTranscribe() : begin())}>
        {mode === "starting" || mode === "transcribing" ? <Loader2 className="h-4 w-4 animate-spin" /> : mode === "recording" ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
      </button>
      {mode !== "idle" && <button type="button" className={style} aria-label="Cancel dictation" onClick={cancel}><X className="h-4 w-4" /></button>}
    </div>
  );
}
