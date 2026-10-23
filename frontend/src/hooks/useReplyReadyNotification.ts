"use client";

import { useEffect, useRef } from "react";

/**
 * Desktop "your reply is ready" — shown by the PAGE itself, no server push.
 *
 * When a chat turn finishes (isLoading true → false with no error) while the
 * tab is hidden and notifications are already granted, show a notification
 * naming the skill. Never the reply text: notifications land on lock screens
 * and in OS notification centres (docs/design/v6.40.0/web-push-notifications.md).
 *
 * This covers a backgrounded desktop tab, whose connection stays alive. A
 * suspended phone app can't run this — that case is the server's Web Push.
 * Uses the service-worker registration when there is one (required on some
 * platforms, and clicks route through sw.js); falls back to `new Notification`.
 */
export function useReplyReadyNotification({
  isLoading,
  hasError,
  skillName,
  sessionId,
}: {
  isLoading: boolean;
  hasError: boolean;
  skillName: string;
  sessionId: string | null;
}) {
  const wasLoading = useRef(false);

  useEffect(() => {
    const finished = wasLoading.current && !isLoading && !hasError;
    wasLoading.current = isLoading;
    if (!finished) return;
    if (typeof document === "undefined" || document.visibilityState !== "hidden") return;
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;

    const url = window.location.pathname + window.location.search;
    const title = "Reply ready";
    const options: NotificationOptions = {
      body: `in ${skillName}`,
      tag: sessionId ?? undefined,
      icon: "/images/logo/icon-192.png",
      data: { url },
    };
    void (async () => {
      try {
        const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
        if (reg) {
          await reg.showNotification(title, options);
          return;
        }
        const n = new Notification(title, options);
        n.onclick = () => {
          window.focus();
          n.close();
        };
      } catch {
        // Best effort: the reply is already on screen for when they come back.
      }
    })();
  }, [isLoading, hasError, skillName, sessionId]);
}
