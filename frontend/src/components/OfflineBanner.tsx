"use client";

import { useEffect, useState } from "react";

/**
 * Says so when the browser loses its connection, and when it comes back, so a
 * request that can't go anywhere never looks like the app hanging.
 */
export function OfflineBanner() {
  const [offline, setOffline] = useState(false);
  const [backOnline, setBackOnline] = useState(false);

  useEffect(() => {
    setOffline(!navigator.onLine);
    let timer: number | undefined;
    const goOffline = () => {
      window.clearTimeout(timer);
      setBackOnline(false);
      setOffline(true);
    };
    const goOnline = () => {
      setOffline(false);
      setBackOnline(true);
      timer = window.setTimeout(() => setBackOnline(false), 3000);
    };
    window.addEventListener("offline", goOffline);
    window.addEventListener("online", goOnline);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("offline", goOffline);
      window.removeEventListener("online", goOnline);
    };
  }, []);

  if (!offline && !backOnline) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className={
        offline
          ? "w-full border-b border-warning/30 bg-warning/10 px-4 py-1.5 text-center text-sm text-warning"
          : "w-full border-b border-success/30 bg-success/10 px-4 py-1.5 text-center text-sm text-success"
      }
    >
      {offline ? "You're offline. Changes and messages won't send until you reconnect." : "Back online."}
    </div>
  );
}
