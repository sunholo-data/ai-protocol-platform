// MULTI-LANG M3 — a skill's text in the person's language.
//
// `xx` is the made-up test locale (sprint ruling 4); no real language ships.

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { PinnedWelcome } from "@/components/chat/PinnedWelcome";
import { SkillSwitcher } from "@/components/navigation/SkillSwitcher";
import { CatalogProvider, LocaleProvider, type LocaleConfig } from "@/i18n";
import { FIXTURE_CATALOG } from "@/i18n/__fixtures__/catalog";
import { USER_LOCALE_KEY, UserLocaleProvider, useUserLocale } from "@/i18n/userLocale";
import { skillGreeting, skillText, useSkillText } from "@/hooks/useSkillText";
import { useSkillMeta } from "@/hooks/useSkillMeta";
import type { Skill, SkillLocales } from "@/types/skill";

const XX: SkillLocales = {
  xx: { display_name: "Xeneral assistant", initial_message: "Xi! Xow can I xelp?" },
};

const BASE = {
  displayName: "General assistant",
  description: "Answers general questions.",
  initialMessage: "Hi! How can I help?",
};

function memoryStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

const TWO: LocaleConfig = { locales: ["en", "xx"], defaultLocale: "en" };

function App({ children, config = TWO }: { children: ReactNode; config?: LocaleConfig }) {
  return (
    <CatalogProvider catalog={FIXTURE_CATALOG}>
      <UserLocaleProvider config={config}>{children}</UserLocaleProvider>
    </CatalogProvider>
  );
}

describe("skillText / useSkillText", () => {
  it("without a locales block every locale gets the base", () => {
    expect(skillText(BASE, "xx")).toEqual(BASE);
    expect(skillText({ ...BASE, locales: null }, "en")).toEqual(BASE);
  });

  it("with a block: the locale's text, the base for a field it doesn't translate", () => {
    expect(skillText({ ...BASE, locales: XX }, "xx")).toEqual({
      displayName: "Xeneral assistant",
      description: "Answers general questions.",
      initialMessage: "Xi! Xow can I xelp?",
    });
    // Someone reading in the source language sees the base, not a translation.
    expect(skillText({ ...BASE, locales: XX }, "en")).toEqual(BASE);
  });

  it("the hook reads the locale in force", () => {
    const wrap = ({ children }: { children: ReactNode }) => <LocaleProvider locale="xx">{children}</LocaleProvider>;
    const { result } = renderHook(() => useSkillText({ ...BASE, locales: XX }), { wrapper: wrap });
    expect(result.current.displayName).toBe("Xeneral assistant");
    const { result: none } = renderHook(() => useSkillText({ ...BASE, locales: XX }));
    expect(none.current.displayName).toBe("General assistant");
  });
});

describe("skillGreeting", () => {
  const intro = { welcome: { introMessage: "Welcome! Pick a document." } };
  it("keeps today's order with no translation: welcome intro, then initial message", () => {
    expect(skillGreeting({ ...BASE, ...intro }, "xx")).toBe("Welcome! Pick a document.");
    expect(skillGreeting(BASE, "xx")).toBe("Hi! How can I help?");
    expect(skillGreeting({ initialMessage: "  " }, "en")).toBeNull();
  });
  it("a translated greeting wins over an untranslated intro", () => {
    expect(skillGreeting({ ...BASE, ...intro, locales: XX }, "xx")).toBe("Xi! Xow can I xelp?");
    expect(skillGreeting({ ...BASE, ...intro, locales: XX }, "en")).toBe("Welcome! Pick a document.");
  });
});

// The chat's wiring in miniature: ChatShell takes the name and greeting from
// useSkillMeta and hands them to the welcome panel.
function Welcome() {
  const { displayName, greeting, loading } = useSkillMeta("skill-1");
  if (loading || !greeting) return null;
  return <PinnedWelcome content={greeting} skillId="skill-1" skillDisplayName={displayName} />;
}

describe("the welcome and the switcher with xx chosen", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
    vi.stubGlobal("sessionStorage", memoryStorage());
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({ ...BASE, locales: { xx: { initial_message: "Xi! Xow can I xelp?" } } }),
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchWithAuth.mockReset();
  });

  it("welcome: the xx greeting, and the base name the skill doesn't translate", async () => {
    localStorage.setItem(USER_LOCALE_KEY, "xx");
    render(
      <App>
        <Welcome />
      </App>,
    );
    expect(await screen.findByText("Xi! Xow can I xelp?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /with General assistant/ })).toBeTruthy();
  });

  it("welcome: English chosen shows the base greeting", async () => {
    render(
      <App>
        <Welcome />
      </App>,
    );
    expect(await screen.findByText("Hi! How can I help?")).toBeTruthy();
  });

  it("useSkillMeta follows a language change without refetching", async () => {
    const { result } = renderHook(() => ({ meta: useSkillMeta("skill-1"), user: useUserLocale() }), { wrapper: App });
    await waitFor(() => expect(result.current.meta.loading).toBe(false));
    expect(result.current.meta.greeting).toBe("Hi! How can I help?");
    act(() => result.current.user.setLocale("xx"));
    expect(result.current.meta.greeting).toBe("Xi! Xow can I xelp?");
    expect(result.current.meta.initialMessage).toBe("Xi! Xow can I xelp?");
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("switcher: xx names where translated, the base name otherwise", async () => {
    localStorage.setItem(USER_LOCALE_KEY, "xx");
    const skill = (over: Partial<Skill>): Skill =>
      ({
        name: "x",
        description: "",
        instructions: "",
        skillMetadata: { author: "", version: "", model: "", tools: [], toolConfigs: {}, subSkills: [] },
        references: {},
        assets: {},
        avatar: "",
        ownerEmail: "",
        ownerId: "uid-1",
        accessControl: { type: "private" },
        protocols: {} as Skill["protocols"],
        initialMessage: "",
        tags: [],
        featured: false,
        usageCount: 0,
        createdAt: 0,
        updatedAt: 0,
        ...over,
      }) as Skill;
    render(
      <App>
        <SkillSwitcher
          activeSkillId="a"
          skills={[
            skill({ skillId: "a", name: "general", displayName: "General assistant", locales: XX }),
            skill({ skillId: "b", name: "research", displayName: "Research" }),
          ]}
        />
      </App>,
    );
    const trigger = await screen.findByRole("button", { name: /Skill: Xeneral assistant/ });
    await act(async () => userEvent.click(trigger));
    expect(await screen.findByRole("link", { name: /Research/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Xeneral assistant/ })).toBeTruthy();
  });
});
