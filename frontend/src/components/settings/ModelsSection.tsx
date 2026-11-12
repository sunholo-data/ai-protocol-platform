"use client";

import { setSpecificModelsOptIn, useModelMenu, useSpecificModelsOptIn } from "@/lib/modelChoice";
import { useT } from "@/i18n";
import { SettingRow } from "./SettingRow";
import { Switch } from "./Switch";

/**
 * Settings → Models: the opt-in for the third level of the composer's Model
 * menu. Everyone gets Auto, Fast and Thorough; turning this on also lists the
 * specific models this deployment allows. Hidden where the deployment offers
 * no specific models (MODEL_CHOICE=off|tiers, or none allowed here).
 */
export function ModelsSection() {
  const menu = useModelMenu();
  const optIn = useSpecificModelsOptIn();
  const t = useT("ModelsSection");
  if (!menu || menu.offered !== "all" || menu.models.length === 0) return null;
  return (
    <section id="models" aria-labelledby="models-title" className="scroll-mt-6">
      <h2 id="models-title" className="text-lg font-semibold text-foreground">
        {t("title")}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("body")}</p>
      <div className="mt-3 divide-y divide-border rounded-[var(--radius)] border border-border">
        <SettingRow title={t("specific")} note={t("specificNote")}>
          <Switch label={t("specific")} checked={optIn} onChange={(next) => setSpecificModelsOptIn(next)} />
        </SettingRow>
      </div>
    </section>
  );
}

/** Whether the Settings list should show a Models entry. */
export function useShowModelsSection(): boolean {
  const menu = useModelMenu();
  return Boolean(menu && menu.offered === "all" && menu.models.length > 0);
}
