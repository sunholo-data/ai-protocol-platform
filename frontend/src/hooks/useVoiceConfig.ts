"use client";

/**
 * useVoiceConfig — fetch the voice provider config for a skill from
 * GET /api/voice/config. Returns the TTS provider + voice the frontend
 * should use (browser-native vs Cloud TTS).
 *
 * Client cache: results are memoized per skillId for the lifetime of the
 * page. The config rarely changes within a session; refetching on every
 * assistant message would be wasteful.
 *
 * Failure mode: on network error, returns the safe default
 * `{ tts: { provider: "browser", ... } }`. The browser-native path always
 * works so the read-aloud button never hard-fails.
 *
 * Ported from the AIPLA fork (voice-provider-abstraction), v6.6.0 M2.
 * STT / recording capability flags were stripped — read-aloud only.
 */

import { useCallback, useEffect, useState } from "react";
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
  loading: boolean;
}

const DEFAULT_CONFIG: Omit<VoiceConfig, "loading"> = {
  tts: {
    provider: "browser",
    voice: null,
    language: null,
    enabled: true,
    capabilities: { tts: true, stt: false, streaming: false, languages: [] },
  },
};

// Module-level cache. Keyed by skillId (or "_default_" for no-skill
// configs). Lives for the page session, refreshed on tab focus so
// skill-author updates land within a tab-switch (not just a hard reload).
const _cache = new Map<string, Omit<VoiceConfig, "loading">>();

export function useVoiceConfig(skillId: string | null): VoiceConfig {
  const cacheKey = skillId ?? "_default_";
  const cached = _cache.get(cacheKey);

  const [config, setConfig] = useState<Omit<VoiceConfig, "loading">>(
    cached ?? DEFAULT_CONFIG,
  );
  const [loading, setLoading] = useState<boolean>(!cached);

  const fetchConfig = useCallback(
    async (signal: { cancelled: boolean }) => {
      const url = skillId
        ? `/api/proxy/api/voice/config?skill_id=${encodeURIComponent(skillId)}`
        : `/api/proxy/api/voice/config`;
      try {
        const res = await fetchWithAuth(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const raw = (await res.json()) as Partial<Omit<VoiceConfig, "loading">>;
        const data: Omit<VoiceConfig, "loading"> = {
          // Merge over defaults so a backend missing `enabled` still yields a
          // boolean (defaults to on).
          tts: raw.tts ? { ...DEFAULT_CONFIG.tts, ...raw.tts } : DEFAULT_CONFIG.tts,
        };
        if (!signal.cancelled) {
          _cache.set(cacheKey, data);
          setConfig(data);
          setLoading(false);
        }
      } catch {
        if (!signal.cancelled) setLoading(false);
      }
    },
    [skillId, cacheKey],
  );

  useEffect(() => {
    const signal = { cancelled: false };
    if (cached) {
      setConfig(cached);
      setLoading(false);
    } else {
      void fetchConfig(signal);
    }
    return () => {
      signal.cancelled = true;
    };
  }, [skillId, cacheKey, cached, fetchConfig]);

  // Refetch when the tab regains focus — gives skill-author voice updates
  // a propagation path short of a full page reload.
  useEffect(() => {
    if (typeof window === "undefined") return;
    function onFocus() {
      const signal = { cancelled: false };
      void fetchConfig(signal);
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [fetchConfig]);

  return { ...config, loading };
}

/** Reset the module-level cache. Test helper; not exported in the bundle. */
export function _resetVoiceConfigCacheForTests(): void {
  _cache.clear();
}
