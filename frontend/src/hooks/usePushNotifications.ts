"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

/**
 * Web Push on/off for this device (docs/design/v6.40.0/web-push-notifications.md).
 *
 * States, in the order they are decided:
 *  - `unsupported`            no service worker / PushManager / Notification
 *                             (old browser, or `next dev` — the worker is only
 *                             registered in production builds)
 *  - `ios-needs-home-screen`  iPhone/iPad Safari tab: iOS only allows push for an
 *                             app added to the Home Screen
 *  - `disabled-by-server`     the deployment has no VAPID keys (`enabled:false`)
 *  - `blocked`                the user denied permission in browser settings
 *  - `off` / `on`             this device is / is not subscribed
 *  - `working`                a request is in flight
 *
 * `enable()` must be called from a click handler — iOS only shows the
 * permission prompt for a real user gesture. Every failure lands in `error`
 * (NEVER SILENT); nothing is swallowed to the console.
 */
export type PushState =
  | "loading"
  | "unsupported"
  | "ios-needs-home-screen"
  | "disabled-by-server"
  | "blocked"
  | "off"
  | "on"
  | "working";

const CONFIG_URL = "/api/proxy/api/push/config";
const SUBSCRIBE_URL = "/api/proxy/api/push/subscribe";
const TEST_URL = "/api/proxy/api/push/test";

/** How long to wait for the service worker before calling push unsupported here. */
const SW_READY_TIMEOUT_MS = 4000;

/** VAPID public key (base64url) → the byte array PushManager expects. */
export function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function isIos(): boolean {
  const ua = navigator.userAgent || "";
  // iPadOS 13+ reports as Mac; a touch-capable "Mac" is an iPad.
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

function isStandalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true || window.matchMedia?.("(display-mode: standalone)").matches === true;
}

function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** The active registration, or null if none shows up in time (e.g. `next dev`). */
async function readyRegistration(): Promise<ServiceWorkerRegistration | null> {
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), SW_READY_TIMEOUT_MS)),
  ]);
}

export function usePushNotifications() {
  const [state, setState] = useState<PushState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [publicKey, setPublicKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const settle = (s: PushState) => {
      if (!cancelled) setState(s);
    };
    (async () => {
      if (!pushSupported()) {
        // An iPhone Safari tab has no PushManager at all — say why.
        settle(typeof window !== "undefined" && isIos() && !isStandalone() ? "ios-needs-home-screen" : "unsupported");
        return;
      }
      if (isIos() && !isStandalone()) return settle("ios-needs-home-screen");
      try {
        const res = await fetchWithAuth(CONFIG_URL);
        const cfg = res.ok ? ((await res.json()) as { enabled?: boolean; publicKey?: string | null }) : null;
        if (!cfg?.enabled || !cfg.publicKey) return settle("disabled-by-server");
        if (!cancelled) setPublicKey(cfg.publicKey);
      } catch {
        return settle("disabled-by-server");
      }
      if (Notification.permission === "denied") return settle("blocked");
      const reg = await readyRegistration();
      if (!reg) return settle("unsupported");
      const existing = await reg.pushManager.getSubscription();
      settle(existing && Notification.permission === "granted" ? "on" : "off");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const enable = useCallback(async () => {
    if (!publicKey) return;
    setError(null);
    setState("working");
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setState(permission === "denied" ? "blocked" : "off");
        if (permission !== "denied") setError("Notifications weren't allowed.");
        return;
      }
      const reg = await readyRegistration();
      if (!reg) throw new Error("The app's background worker isn't running — reload and try again.");
      const sub =
        (await reg.pushManager.getSubscription()) ??
        (await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
        }));
      const res = await fetchWithAuth(SUBSCRIBE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON() }),
      });
      if (!res.ok) throw new Error(`Couldn't register this device (${res.status}).`);
      setState("on");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("off");
    }
  }, [publicKey]);

  const disable = useCallback(async () => {
    setError(null);
    setState("working");
    try {
      const reg = await readyRegistration();
      const sub = reg ? await reg.pushManager.getSubscription() : null;
      if (sub) {
        // Tell the server first: the endpoint is the key it stores.
        await fetchWithAuth(SUBSCRIBE_URL, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        await sub.unsubscribe();
      }
      setState("off");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("on");
    }
  }, []);

  const test = useCallback(async () => {
    setError(null);
    try {
      const res = await fetchWithAuth(TEST_URL, { method: "POST" });
      if (!res.ok) throw new Error(`The test notification couldn't be sent (${res.status}).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  return { state, error, enable, disable, test };
}
