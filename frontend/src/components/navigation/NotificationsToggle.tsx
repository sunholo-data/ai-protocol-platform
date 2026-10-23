"use client";

import { Bell, BellOff } from "lucide-react";
import { usePushNotifications } from "@/hooks/usePushNotifications";

/**
 * The "Notifications" row in the account menus — the phone menu sheet
 * (AccountPanel) and the desktop avatar menu (UserMenu). Hidden entirely when
 * push can't work here for a reason the user can't act on (no browser support,
 * or the deployment hasn't configured push); otherwise always says what state
 * it is in and what to do next.
 */
export function NotificationsToggle({ rowClassName }: { rowClassName: string }) {
  const { state, error, enable, disable, test } = usePushNotifications();
  if (state === "loading" || state === "unsupported" || state === "disabled-by-server") return null;

  const note = (text: string) => (
    <p className="flex min-h-[44px] items-center gap-3 px-3 text-sm text-muted-foreground md:min-h-[36px]">
      <BellOff className="h-4 w-4 shrink-0" aria-hidden /> {text}
    </p>
  );

  return (
    <div data-testid="notifications-toggle">
      {state === "ios-needs-home-screen" && note("Add to Home Screen to get notifications")}
      {state === "blocked" && note("Notifications are blocked in your browser settings")}
      {(state === "off" || state === "working") && (
        <button type="button" className={rowClassName} onClick={() => void enable()} disabled={state === "working"}>
          <Bell className="h-4 w-4 text-muted-foreground" aria-hidden />
          {state === "working" ? "Turning on…" : "Turn on notifications"}
        </button>
      )}
      {state === "on" && (
        <div className="flex items-center gap-1">
          <button type="button" className={rowClassName} onClick={() => void disable()} title="Turn off notifications">
            <Bell className="h-4 w-4 text-primary" aria-hidden /> Notifications on
          </button>
          <button
            type="button"
            onClick={() => void test()}
            className="min-h-[44px] shrink-0 rounded-[var(--radius)] px-3 text-sm text-primary underline underline-offset-2 hover:bg-muted md:min-h-[36px]"
          >
            Send a test
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="px-3 pb-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
