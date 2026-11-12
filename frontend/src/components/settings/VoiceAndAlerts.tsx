"use client";

import { useAutoReadAloud } from "@/hooks/useAutoReadAloud";
import { usePushNotifications } from "@/hooks/usePushNotifications";
import { useT } from "@/i18n";
import { SettingRow } from "./SettingRow";
import { Switch } from "./Switch";

const READ_ALOUD = process.env.NEXT_PUBLIC_ENABLE_READ_ALOUD === "true";

/**
 * Read replies aloud and push notifications. Either row disappears when it
 * can't work here for a reason the user can't act on; the section disappears
 * when both do.
 */
export function VoiceAndAlerts() {
  const autoRead = useAutoReadAloud();
  const push = usePushNotifications();
  const t = useT("VoiceAndAlerts");
  const showPush = !(push.state === "loading" || push.state === "unsupported" || push.state === "disabled-by-server");
  if (!READ_ALOUD && !showPush) return null;

  return (
    <section id="voice" aria-labelledby="voice-title" className="scroll-mt-6">
      <h2 id="voice-title" className="text-lg font-semibold text-foreground">
        {t("title")}
      </h2>
      <div className="mt-3 divide-y divide-border rounded-[var(--radius)] border border-border">
        {READ_ALOUD ? (
          <SettingRow title={t("readAloud")} note={t("readAloudNote")}>
            <Switch label={t("readAloud")} checked={autoRead.enabled} onChange={() => autoRead.toggle()} />
          </SettingRow>
        ) : null}
        {showPush ? <NotificationsRow push={push} /> : null}
      </div>
    </section>
  );
}

function NotificationsRow({ push }: { push: ReturnType<typeof usePushNotifications> }) {
  const { state, error, enable, disable, test } = push;
  const t = useT("VoiceAndAlerts");
  const note =
    state === "blocked" ? (
      <span className="text-warning">{t("notificationsBlocked")}</span>
    ) : state === "ios-needs-home-screen" ? (
      t("notificationsIos")
    ) : state === "on" ? (
      <button type="button" onClick={() => void test()} className="underline underline-offset-2 hover:text-foreground">
        {t("notificationsTest")}
      </button>
    ) : (
      t("notificationsNote")
    );
  return (
    <div data-testid="notifications-setting">
      <SettingRow title={t("notifications")} note={note}>
        <Switch
          label={t("notifications")}
          checked={state === "on"}
          disabled={state === "blocked" || state === "ios-needs-home-screen" || state === "working"}
          onChange={(next) => void (next ? enable() : disable())}
        />
      </SettingRow>
      {error ? (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
