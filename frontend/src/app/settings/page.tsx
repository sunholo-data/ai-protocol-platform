"use client";

import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { MemoryList } from "@/components/memory/MemoryList";
import { YourInstructionsCard } from "@/components/projects/YourInstructionsCard";
import { McpTokensSection } from "@/components/settings/McpTokensSection";
import { ModelsSection, useShowModelsSection } from "@/components/settings/ModelsSection";
import { VoiceAndAlerts } from "@/components/settings/VoiceAndAlerts";
import { PageLoading } from "@/components/ui/skeleton";
import { LanguageSection, useShowLanguageSection } from "@/components/settings/LanguageSwitch";
import { useT } from "@/i18n";

const SECTIONS = [
  { id: "language", label: "navLanguage" },
  { id: "instructions", label: "navInstructions" },
  { id: "memory", label: "navMemory" },
  { id: "voice", label: "navVoice" },
  { id: "models", label: "navModels" },
  { id: "connections", label: "navConnections" },
] as const;

/**
 * Settings: one page for everything personal. The old addresses
 * (/settings/instructions, /memory, /mcp) redirect to their section.
 */
export default function SettingsPage() {
  const { user, loading } = useAuth();
  const showModels = useShowModelsSection();
  const showLanguage = useShowLanguageSection();
  const t = useT("SettingsPage");
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <PageLoading />;
  return (
    <div className="mx-auto flex w-full max-w-4xl gap-10 px-4 py-8 md:px-6">
      <nav aria-label={t("navLabel")} className="hidden w-44 shrink-0 md:block">
        <div className="sticky top-6 space-y-0.5">
          {SECTIONS.filter((s) => (s.id !== "models" || showModels) && (s.id !== "language" || showLanguage)).map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className="block rounded-[var(--radius)] px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {t(s.label)}
            </a>
          ))}
        </div>
      </nav>
      <main className="min-w-0 flex-1 space-y-10">
        <h1 className="text-2xl font-semibold text-foreground">{t("title")}</h1>

        <LanguageSection />

        <section id="instructions" aria-labelledby="instructions-title" className="scroll-mt-6">
          <h2 id="instructions-title" className="text-lg font-semibold text-foreground">
            {t("instructionsTitle")}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("instructionsBody")}</p>
          <YourInstructionsCard plain className="mt-3" />
        </section>

        <section id="memory" aria-labelledby="memory-title" className="scroll-mt-6">
          <h2 id="memory-title" className="text-lg font-semibold text-foreground">
            {t("memoryTitle")}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("memoryBody")}</p>
          <div className="mt-3">
            <MemoryList />
          </div>
        </section>

        <VoiceAndAlerts />

        <ModelsSection />

        <McpTokensSection />
      </main>
    </div>
  );
}
