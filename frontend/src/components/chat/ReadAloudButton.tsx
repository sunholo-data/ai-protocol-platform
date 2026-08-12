"use client";

/**
 * ReadAloudButton — click-to-play TTS control for assistant messages.
 *
 * v6.6.0 M2 (ported from the AIPLA fork). Each assistant turn in the chat
 * renders one of these. Click to hear the message spoken; click again to
 * stop. This is a CLICK-TO-PLAY control only — it never auto-speaks on
 * mount (the fork's auto-read-on-mount behaviour was intentionally dropped).
 *
 * Two synthesis paths:
 *  - provider === "browser": speak locally via window.speechSynthesis.
 *  - provider === "gcp_*": split text into paragraph-sized chunks, POST
 *    each to /api/voice/tts/synthesize and play them sequentially with
 *    pipeline prefetch (chunk N+1 fetches while chunk N plays). The
 *    backend cache-first path keeps replays free.
 *
 * Barge-in: clicking one button dispatches `aitana:voice.cancel`, which
 * every other instance listens for and stops on, so two bubbles can't
 * overlap.
 */

import { VolumeX } from "lucide-react";
import { VoiceIcon } from "@/components/icons";
import { useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

interface ReadAloudButtonProps {
  /** Text to speak. Stripped of markdown / HTML before utterance. */
  text: string;
  /** BCP-47 language tag (e.g. "es", "en"). Defaults to "es" (Aitana). */
  lang?: string;
  /** Voice provider. `"browser"` uses window.speechSynthesis. `"gcp_wavenet"`
   * etc. POST to /api/voice/tts/synthesize. From useVoiceConfig in the
   * parent. */
  provider?: string;
  /** Optional provider-specific voice name (e.g. `"es-ES-Wavenet-C"`).
   * Only used when provider is a non-browser tier. */
  voice?: string | null;
  /** Optional skill id passed through to the synthesize endpoint so
   * server-side cost spans tag the right skill. */
  skillId?: string;
  /** Optional className to control sizing / colour from the parent. */
  className?: string;
}

function isSpeechSynthesisAvailable(): boolean {
  if (typeof window === "undefined") return false;
  // mobile Safari has the property but speak() may no-op without a user
  // gesture; we ship the button anyway and let the gesture work. Check the
  // value (not just the property's existence) — tests stub
  // `window.speechSynthesis = undefined` to simulate older browsers.
  return (
    Boolean((window as Window & { speechSynthesis?: unknown }).speechSynthesis) &&
    typeof window.SpeechSynthesisUtterance !== "undefined"
  );
}

/** Strip markdown / LaTeX / emoji so the TTS engine doesn't read raw syntax
 *  aloud ("**bold**" → "star star bold star star"). Conservative enough to
 *  keep math VALUES readable.
 *
 *  NOTE: collapses only horizontal whitespace (spaces/tabs) so the caller
 *  can split on paragraph breaks (\n\n) BEFORE calling this.
 */
function plainTextForSpeech(text: string): string {
  return text
    // Block LaTeX: $$...$$ and \[ ... \]
    .replace(/\$\$[\s\S]*?\$\$/g, " ")
    .replace(/\\\[[\s\S]*?\\\]/g, " ")
    // Inline LaTeX: $...$
    .replace(/\$([^$\n]+)\$/g, " $1 ")
    // Code fences
    .replace(/```[\s\S]*?```/g, " ")
    // Inline markdown decoration
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    // Headings + list bullets at start of line
    .replace(/^#+\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    // Block-quote markers + horizontal rules
    .replace(/^>\s?/gm, "")
    .replace(/^-{3,}|_{3,}|\*{3,}$/gm, "")
    // Emoji + dingbats + arrows
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2300}-\u{23FF}]/gu, " ")
    // Collapse horizontal whitespace only (preserve \n for sentence detection)
    .replace(/[ \t]+/g, " ")
    .replace(/\n /g, "\n")
    .trim();
}

// GCP Chirp3HD synthesises ~1 char/ms; 600 chars ≈ 30–45 s of speech.
// Keeping chunks small lets the first audio start quickly and gives the
// prefetch pipeline time to fetch ahead without gaps.
const CHUNK_MAX_CHARS = 600;
// Merge sub-chunks shorter than this with the next to avoid many tiny calls.
const CHUNK_MIN_MERGE = 150;

/** Find the last sentence boundary (. ! ?) at or before `near` chars. */
function splitAtSentences(text: string, maxChars: number): string[] {
  if (text.length <= maxChars) return [text];
  const result: string[] = [];
  let remaining = text;
  while (remaining.length > maxChars) {
    const slice = remaining.slice(0, maxChars);
    const lastEnd = Math.max(slice.lastIndexOf(". "), slice.lastIndexOf("! "), slice.lastIndexOf("? "));
    // Cut after the punctuation (include it); if no boundary, hard-cut.
    const cut = lastEnd > 20 ? lastEnd + 1 : maxChars;
    result.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) result.push(remaining);
  return result;
}

/**
 * Split raw markdown text into speech-ready chunks:
 *   1. Split at paragraph boundaries (\n\n) BEFORE cleaning so structure is
 *      preserved for natural pacing.
 *   2. Clean each paragraph with plainTextForSpeech.
 *   3. Split paragraphs that exceed CHUNK_MAX_CHARS at sentence boundaries.
 *   4. Merge short consecutive sub-chunks to avoid many tiny API calls.
 */
function splitIntoSpeechChunks(rawText: string): string[] {
  const paragraphs = rawText.split(/\n{2,}/);
  const subChunks: string[] = [];
  for (const para of paragraphs) {
    const cleaned = plainTextForSpeech(para);
    if (!cleaned) continue;
    subChunks.push(...splitAtSentences(cleaned, CHUNK_MAX_CHARS));
  }
  if (subChunks.length === 0) return [];
  // Merge short sub-chunks with the next to avoid many tiny API calls.
  const merged: string[] = [];
  let acc = "";
  for (const chunk of subChunks) {
    if (!acc) {
      acc = chunk;
    } else if (acc.length < CHUNK_MIN_MERGE && acc.length + 1 + chunk.length <= CHUNK_MAX_CHARS) {
      acc += " " + chunk;
    } else {
      merged.push(acc);
      acc = chunk;
    }
  }
  if (acc) merged.push(acc);
  return merged;
}

export function ReadAloudButton({
  text,
  lang = "es",
  provider = "browser",
  voice = null,
  skillId,
  className,
}: ReadAloudButtonProps) {
  const useGCP = provider !== "browser";
  // We only need Web Speech availability for the browser-native path. The
  // GCP path uses the standard Audio() element, available everywhere.
  const [available] = useState<boolean>(() => (useGCP ? true : isSpeechSynthesisAvailable()));
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Legacy single-fetch URL (kept for cleanup safety; pipeline uses pendingUrls).
  const audioUrlRef = useRef<string | null>(null);
  // Legacy single-fetch abort (browser-path fallback and old callers).
  const fetchAbortRef = useRef<AbortController | null>(null);
  // Chunked-pipeline state:
  //   chunkPipelineAbortRef — call to cancel fetches + mark cancelled.
  //   chunkPlayResolveRef  — call to unblock the current chunk's play-await.
  const chunkPipelineAbortRef = useRef<(() => void) | null>(null);
  const chunkPlayResolveRef = useRef<(() => void) | null>(null);

  // Cancel any in-flight utterance / audio on unmount so navigating away
  // mid-speech doesn't leave the OS talking or leak the blob URL.
  useEffect(() => {
    return () => {
      chunkPipelineAbortRef.current?.();
      chunkPipelineAbortRef.current = null;
      chunkPlayResolveRef.current?.();
      chunkPlayResolveRef.current = null;
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        try {
          window.speechSynthesis.cancel();
        } catch {
          // No-op: some browsers throw if there's nothing to cancel.
        }
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
        audioUrlRef.current = null;
      }
      if (fetchAbortRef.current) {
        fetchAbortRef.current.abort();
        fetchAbortRef.current = null;
      }
    };
  }, []);

  // Barge-in: listen for the global voice.cancel event so clicking another
  // button (or navigating) stops in-flight audio here.
  useEffect(() => {
    if (typeof window === "undefined") return;
    function onCancel() {
      stopAll();
    }
    window.addEventListener("aitana:voice.cancel", onCancel);
    return () => window.removeEventListener("aitana:voice.cancel", onCancel);
  }, []);

  if (!available) {
    return null;
  }

  function stopAll() {
    // Cancel the chunked pipeline first, then unblock the play-await.
    chunkPipelineAbortRef.current?.();
    chunkPipelineAbortRef.current = null;
    chunkPlayResolveRef.current?.();
    chunkPlayResolveRef.current = null;

    try {
      window.speechSynthesis?.cancel();
    } catch {
      // Ignore.
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    if (fetchAbortRef.current) {
      fetchAbortRef.current.abort();
      fetchAbortRef.current = null;
    }
    utteranceRef.current = null;
    setIsSpeaking(false);
  }

  function speakViaBrowser(cleanText: string) {
    const utt = new SpeechSynthesisUtterance(cleanText);
    utt.lang = lang;
    utt.onend = stopAll;
    utt.onerror = stopAll;
    utteranceRef.current = utt;
    try {
      window.speechSynthesis.speak(utt);
      setIsSpeaking(true);
    } catch {
      stopAll();
    }
  }

  async function speakViaGCP(): Promise<void> {
    const chunks = splitIntoSpeechChunks(text);
    if (chunks.length === 0) return;

    let cancelled = false;
    const activeAborts: AbortController[] = [];
    const pendingUrls: string[] = [];

    // Register cancel so stopAll() can interrupt the pipeline from outside.
    chunkPipelineAbortRef.current = () => {
      cancelled = true;
      for (const c of activeAborts) c.abort();
      for (const u of pendingUrls) URL.revokeObjectURL(u);
    };

    async function fetchChunk(chunkText: string): Promise<string | null> {
      const controller = new AbortController();
      activeAborts.push(controller);
      try {
        const res = await fetchWithAuth("/api/proxy/api/voice/tts/synthesize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: chunkText, lang, voice, skillId }),
          signal: controller.signal,
        });
        if (cancelled || !res.ok) return null;
        const contentType = res.headers.get("content-type") ?? "";
        if (contentType.startsWith("application/json")) return "__browser__";
        const blob = await res.blob();
        if (cancelled) return null;
        const url = URL.createObjectURL(blob);
        pendingUrls.push(url);
        return url;
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return null;
        return null;
      }
    }

    // Show the stop button as soon as the user clicks — don't wait for audio.
    setIsSpeaking(true);

    // Pipeline: start fetching chunk 0 immediately.
    let prefetch = fetchChunk(chunks[0]);

    for (let i = 0; i < chunks.length; i++) {
      if (cancelled) break;
      const url = await prefetch;
      if (cancelled || url === null) break;

      if (url === "__browser__") {
        // GCP told us to use browser — hand off remaining chunks locally.
        speakViaBrowser(chunks.slice(i).join(" "));
        return;
      }

      // Prefetch the next chunk while this one plays (pipeline).
      if (i + 1 < chunks.length) prefetch = fetchChunk(chunks[i + 1]);

      // Await playback. chunkPlayResolveRef lets stopAll() unblock this
      // promise early when the user cancels mid-chunk.
      await new Promise<void>((resolve) => {
        chunkPlayResolveRef.current = resolve;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          chunkPlayResolveRef.current = null;
          resolve();
        };
        audio.onerror = () => {
          chunkPlayResolveRef.current = null;
          resolve();
        };
        audio.play().catch(() => {
          chunkPlayResolveRef.current = null;
          resolve();
        });
      });

      // Release this chunk's blob URL once playback finishes (or is cancelled).
      URL.revokeObjectURL(url);
      const idx = pendingUrls.indexOf(url);
      if (idx !== -1) pendingUrls.splice(idx, 1);
    }

    if (!cancelled) stopAll();
  }

  function handleClick() {
    if (isSpeaking) {
      stopAll();
      return;
    }
    // Barge-in: cancel any other ReadAloudButton currently speaking before
    // we start, so two bubbles can't overlap. Each instance's voice.cancel
    // listener does the actual stop work.
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("aitana:voice.cancel"));
    }
    if (useGCP) {
      void speakViaGCP();
      return;
    }
    speakViaBrowser(plainTextForSpeech(text));
  }

  const label = isSpeaking ? "Stop reading aloud" : "Read aloud";
  const Icon = isSpeaking ? VolumeX : VoiceIcon;
  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={label}
      title={label}
      className={
        className ??
        "inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
      }
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
    </button>
  );
}
