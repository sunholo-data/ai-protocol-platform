// MULTI-LANG M3 — Skill Studio's language tabs. `xx` is the made-up test locale.

import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { CatalogProvider, type LocaleConfig } from "@/i18n";
import { FIXTURE_CATALOG } from "@/i18n/__fixtures__/catalog";
import { UserLocaleProvider } from "@/i18n/userLocale";
import type { SkillLocales } from "@/types/skill";
import { SkillLanguageTabs } from "../SkillLanguageTabs";
import { localesForSave, pruneLocales, setLocaleField } from "../skillLocales";

const BASE = { displayName: "General assistant", description: "Answers general questions.", greeting: "Hi! How can I help?" };
const TWO: LocaleConfig = { locales: ["en", "xx"], defaultLocale: "en" };

/** The Studio's wiring in miniature: draft locales in state, Save = localesForSave. */
function Harness({ initial, config = TWO }: { initial?: SkillLocales; config?: LocaleConfig }) {
  const [locales, setLocales] = useState<SkillLocales | undefined>(initial);
  const saved = localesForSave(locales, initial !== undefined);
  return (
    <CatalogProvider catalog={FIXTURE_CATALOG}>
      <UserLocaleProvider config={config}>
        <SkillLanguageTabs base={BASE} locales={locales} onLocalesChange={setLocales}>
          <p>original editor</p>
        </SkillLanguageTabs>
        <output data-testid="save">{saved === undefined ? "(no locales key)" : JSON.stringify(saved)}</output>
      </UserLocaleProvider>
    </CatalogProvider>
  );
}

const save = () => screen.getByTestId("save").textContent;

describe("SkillLanguageTabs", () => {
  it("shows no tabs when the deployment offers one language", () => {
    render(<Harness config={{ locales: ["en"], defaultLocale: "en" }} />);
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByText("original editor")).toBeTruthy();
    expect(save()).toBe("(no locales key)");
  });

  it("the original tab comes first and holds the existing editor", () => {
    render(<Harness />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Original", "xx"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tabpanel").textContent).toContain("original editor");
  });

  it("a language tab shows the original text as each field's placeholder", async () => {
    render(<Harness />);
    await act(async () => userEvent.click(screen.getByRole("tab", { name: "xx" })));
    expect(screen.getByLabelText("Display name").getAttribute("placeholder")).toBe("General assistant");
    expect(screen.getByLabelText("Description").getAttribute("placeholder")).toBe("Answers general questions.");
    expect(screen.getByLabelText(/Greeting/).getAttribute("placeholder")).toBe("Hi! How can I help?");
    // The instructions are not on a language tab.
    expect(screen.queryByText("original editor")?.closest("[hidden]")).toBeTruthy();
  });

  it("filling one field saves only that field", async () => {
    render(<Harness />);
    await act(async () => userEvent.click(screen.getByRole("tab", { name: "xx" })));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Xeneral assistant" } });
    expect(save()).toBe(JSON.stringify({ xx: { display_name: "Xeneral assistant" } }));
  });

  it("clearing the last field removes the language, and Save clears it on the server", async () => {
    render(<Harness initial={{ xx: { display_name: "Xeneral assistant" } }} />);
    await act(async () => userEvent.click(screen.getByRole("tab", { name: "xx" })));
    const input = screen.getByLabelText("Display name") as HTMLInputElement;
    expect(input.value).toBe("Xeneral assistant");
    fireEvent.change(input, { target: { value: "" } });
    expect(save()).toBe("{}");
  });

  it("an untouched skill with no translations sends no locales key", async () => {
    render(<Harness />);
    await act(async () => userEvent.click(screen.getByRole("tab", { name: "xx" })));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "" } });
    expect(save()).toBe("(no locales key)");
  });

  it("arrow keys move between tabs", () => {
    render(<Harness />);
    const [original, xx] = screen.getAllByRole("tab");
    original.focus();
    fireEvent.keyDown(original, { key: "ArrowRight" });
    expect(xx.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(xx);
    expect(xx.tabIndex).toBe(0);
    expect(original.tabIndex).toBe(-1);
    fireEvent.keyDown(xx, { key: "Home" });
    expect(original.getAttribute("aria-selected")).toBe("true");
  });
});

describe("skillLocales helpers", () => {
  it("setLocaleField keeps only filled fields", () => {
    let l = setLocaleField(undefined, "xx", "display_name", "X");
    l = setLocaleField(l, "xx", "description", "Xescription");
    expect(l).toEqual({ xx: { display_name: "X", description: "Xescription" } });
    l = setLocaleField(l, "xx", "display_name", "");
    expect(l).toEqual({ xx: { description: "Xescription" } });
    expect(setLocaleField(l, "xx", "description", "")).toBeUndefined();
  });

  it("pruneLocales drops whitespace-only fields and empty languages", () => {
    expect(pruneLocales({ xx: { display_name: "  " }, yy: { description: "Y", initial_message: "" } })).toEqual({
      yy: { description: "Y" },
    });
  });

  it("localesForSave: nothing to send, a full map, or {} to clear", () => {
    expect(localesForSave(undefined, false)).toBeUndefined();
    expect(localesForSave({ xx: { display_name: " " } }, false)).toBeUndefined();
    expect(localesForSave(undefined, true)).toEqual({});
    expect(localesForSave({ xx: { display_name: "X" } }, false)).toEqual({ xx: { display_name: "X" } });
  });
});
