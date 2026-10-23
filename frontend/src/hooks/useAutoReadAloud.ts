"use client";

/** AIPLA's auto-read preference, shared by every control and reply bubble. */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "platform.voice.auto_read";
const CHANGE_EVENT = "platform:voice.preference";
export const VOICE_CANCEL_EVENT = "aitana:voice.cancel";
let enabled = false;

function readPreference() {
  try {
    enabled = window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch { /* Keep the in-memory preference when storage is unavailable. */ }
  return enabled;
}

function subscribe(listener: () => void) {
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener("storage", listener);
  };
}

export function cancelVoicePlayback() {
  window.dispatchEvent(new CustomEvent(VOICE_CANCEL_EVENT));
}

function toggle() {
  enabled = !readPreference();
  try { window.localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0"); } catch { /* Session only. */ }
  if (!enabled) cancelVoicePlayback();
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function useAutoReadAloud() {
  return {
    enabled: useSyncExternalStore(subscribe, readPreference, () => false),
    toggle,
    cancelInFlight: cancelVoicePlayback,
  };
}
