"use client";

/**
 * useVoiceConfig — fetch the voice provider config for a skill from
 * GET /api/voice/config. Returns the TTS provider + voice the frontend
 * should use (browser-native vs Cloud TTS).
 *
 * ONE request per skillId, however many components ask. This hook is called
 * from every MessageBubble, and the first version fetched per instance and
 * registered a window-focus refetch per instance — so a 40-message thread
 * fired 40 requests on mount (nothing was cached yet) and 40 more on every
 * alt-tab. On prod that made /api/voice/config 38% of all successful requests
 * (1,157 in a week against 18 chat turns), in bursts with 0s gaps. Now a
 * module-level store dedupes in-flight fetches, and a single focus listener
 * refreshes each key once.
 *
 * Failure mode: on network error, returns the safe default
 * `{ tts: { provider: "browser", ... } }`. The browser-native path always
 * works so the read-aloud button never hard-fails.
 *
 * Ported from the AIPLA fork (voice-provider-abstraction), v6.6.0 M2.
 * Dictation is opt-in; unavailable config always disables voice input.
 */

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

export interface VoiceCapabilities {
  tts: boolean;
  stt: boolean;
  streaming: boolean;
  languages: readonly string[];
}

export interface VoiceConfig {
  tts: {
    provider: string;
    voice: string | null;
    /** Skill-resolved language hint from the backend (BCP-47 short form,
     * e.g. "es"). The platform default is "es" (Aitana). */
    language: string | null;
    /** Whether read-aloud is enabled for this skill. Default true. */
    enabled: boolean;
    capabilities: VoiceCapabilities;
  };
  stt: {
    provider: string;
    enabled: boolean;
    capabilities: VoiceCapabilities;
    maxDurationSeconds: number;
  };
  loading: boolean;
}

type Resolved = Omit<VoiceConfig, "loading">;

const DEFAULT_CONFIG: Resolved = {
  stt: {
    provider: "disabled", enabled: false, maxDurationSeconds: 60,
    capabilities: { tts: false, stt: false, streaming: false, languages: [] },
  },
  tts: {
    provider: "browser",
    voice: null,
    language: null,
    enabled: true,
    capabilities: { tts: true, stt: false, streaming: false, languages: [] },
  },
};

const DEFAULT_KEY = "_default_";

// --- module-level store -----------------------------------------------------
//
// Lives for the page session. `inflight` is what dedupes: N components asking
// for the same key while a request is open all await that one promise.

const _cache = new Map<string, Resolved>();
const _inflight = new Map<string, Promise<void>>();
const _failed = new Set<string>();
const _listeners = new Map<string, Set<() => void>>();

function _notify(key: string): void {
  _listeners.get(key)?.forEach((fn) => fn());
}

function _fetchKey(key: string): Promise<void> {
  const open = _inflight.get(key);
  if (open) return open;
  const url =
    key === DEFAULT_KEY
      ? `/api/proxy/api/voice/config`
      : `/api/proxy/api/voice/config?skill_id=${encodeURIComponent(key)}`;
  const p = (async () => {
    try {
      const res = await fetchWithAuth(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const raw = (await res.json()) as Partial<Resolved>;
      _cache.set(key, {
        stt: raw.stt ? { ...DEFAULT_CONFIG.stt, ...raw.stt } : DEFAULT_CONFIG.stt,
        // Merge over defaults so a backend missing `enabled` still yields a
        // boolean (defaults to on).
        tts: raw.tts ? { ...DEFAULT_CONFIG.tts, ...raw.tts } : DEFAULT_CONFIG.tts,
      });
      _failed.delete(key);
    } catch {
      // Keep whatever we had (or nothing); subscribers fall back to the
      // browser provider. Not cached, so the next subscriber retries.
      _failed.add(key);
    } finally {
      _inflight.delete(key);
      _notify(key);
    }
  })();
  _inflight.set(key, p);
  return p;
}

function _subscribe(key: string, fn: () => void): () => void {
  let set = _listeners.get(key);
  if (!set) {
    set = new Set();
    _listeners.set(key, set);
  }
  set.add(fn);
  _ensureFocusListener();
  return () => {
    set!.delete(fn);
    if (set!.size === 0) _listeners.delete(key);
  };
}

// Refetch when the tab regains focus — gives skill-author voice updates a
// propagation path short of a full page reload. ONE listener for the page,
// refreshing each key that has a live subscriber exactly once.
let _focusListenerInstalled = false;
function _ensureFocusListener(): void {
  if (_focusListenerInstalled || typeof window === "undefined") return;
  _focusListenerInstalled = true;
  window.addEventListener("focus", () => {
    for (const key of _listeners.keys()) void _fetchKey(key);
  });
}

export function useVoiceConfig(skillId: string | null): VoiceConfig {
  const key = skillId ?? DEFAULT_KEY;
  const [, bump] = useState(0);

  useEffect(() => {
    const unsubscribe = _subscribe(key, () => bump((n) => n + 1));
    if (!_cache.has(key)) void _fetchKey(key);
    return unsubscribe;
  }, [key]);

  const cached = _cache.get(key);
  return {
    ...(cached ?? DEFAULT_CONFIG),
    loading: !cached && !_failed.has(key),
  };
}

/** Reset the module-level store. Test helper; not exported in the bundle. */
export function _resetVoiceConfigCacheForTests(): void {
  _cache.clear();
  _inflight.clear();
  _failed.clear();
  _listeners.clear();
  _focusListenerInstalled = false;
}
