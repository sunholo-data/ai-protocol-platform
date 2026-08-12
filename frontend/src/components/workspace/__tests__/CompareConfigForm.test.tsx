// PPA-COMPARE-LAUNCHER M3 — CompareConfigForm tests
//
// The form's contract: emit a `CompareConfig` that OMITS every key left at its
// default so a full-scope run reuses the legacy (non-variant) cache keys.
//   * all clauses selected  -> no `clauses` key
//   * depth 20 (default)     -> no `max_other_clauses` key
//   * severity "all"         -> no `severity_floor` key
// => defaults submit `{}`. A narrowed scope carries only the changed keys.

import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompareConfigForm } from "../CompareConfigForm";
import type { CompareConfig } from "../CompareConfigForm";

function renderForm(props: Partial<React.ComponentProps<typeof CompareConfigForm>> = {}) {
  const onSubmit = vi.fn<[CompareConfig], void>();
  const onCancel = vi.fn();
  const utils = render(<CompareConfigForm onSubmit={onSubmit} onCancel={onCancel} {...props} />);
  return { ...utils, onSubmit, onCancel };
}

function apply(): void {
  fireEvent.click(screen.getByRole("button", { name: /apply scope/i }));
}

function clauseCheckbox(label: RegExp): HTMLInputElement {
  return screen.getByRole("checkbox", { name: label }) as HTMLInputElement;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CompareConfigForm", () => {
  it("defaults to all 12 clauses selected, severity All, depth 20", () => {
    renderForm();
    // All 12 clause checkboxes checked.
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes).toHaveLength(12);
    expect(boxes.every((b) => b.checked)).toBe(true);
    // "All" severity active.
    expect(screen.getByRole("radio", { name: /^all$/i })).toHaveAttribute("aria-checked", "true");
    // Depth shows the default 20.
    expect((screen.getByRole("spinbutton", { name: /depth/i }) as HTMLInputElement).value).toBe("20");
    expect(screen.getByText(/all clauses/i)).toBeInTheDocument();
  });

  it("submits an EMPTY config when everything is left at defaults (legacy cache keys)", () => {
    const { onSubmit } = renderForm();
    apply();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toEqual({});
  });

  it("emits only the selected clause subset (in canonical order) when narrowed", () => {
    const { onSubmit } = renderForm();
    // Clear all, then pick two out of canonical order to prove ordering.
    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    fireEvent.click(clauseCheckbox(/price formula/i));
    fireEvent.click(clauseCheckbox(/settlement type/i));
    apply();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const config = onSubmit.mock.calls[0][0];
    // Canonical order has settlement_type BEFORE price_formula.
    expect(config.clauses).toEqual(["settlement_type", "price_formula"]);
    // Untouched knobs stay omitted.
    expect(config).not.toHaveProperty("severity_floor");
    expect(config).not.toHaveProperty("max_other_clauses");
  });

  it("omits the clauses key when all clauses remain selected", () => {
    const { onSubmit } = renderForm();
    // Toggle one off and back on -> still all selected.
    fireEvent.click(clauseCheckbox(/termination/i));
    fireEvent.click(clauseCheckbox(/termination/i));
    apply();
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty("clauses");
  });

  it("carries severity_floor only when a non-'all' focus is chosen", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(screen.getByRole("radio", { name: /material only/i }));
    apply();
    expect(onSubmit.mock.calls[0][0]).toEqual({ severity_floor: "material" });
  });

  it("maps '+ Moderate' to severity_floor moderate", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(screen.getByRole("radio", { name: /moderate/i }));
    apply();
    expect(onSubmit.mock.calls[0][0]).toEqual({ severity_floor: "moderate" });
  });

  it("carries max_other_clauses only when changed from the default 20", () => {
    const { onSubmit } = renderForm();
    const depth = screen.getByRole("spinbutton", { name: /depth/i }) as HTMLInputElement;
    fireEvent.change(depth, { target: { value: "5" } });
    apply();
    expect(onSubmit.mock.calls[0][0]).toEqual({ max_other_clauses: 5 });
  });

  it("blocks Apply and shows guidance when no clause is selected", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    expect(screen.getByRole("button", { name: /apply scope/i })).toBeDisabled();
    apply();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/select at least one clause/i)).toBeInTheDocument();
  });

  it("restores an existing subset config when re-opened", () => {
    renderForm({ initialConfig: { clauses: ["settlement_type"], severity_floor: "material", max_other_clauses: 3 } });
    // Only settlement_type checked.
    expect(clauseCheckbox(/settlement type/i)).toBeChecked();
    expect(clauseCheckbox(/price formula/i)).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /material only/i })).toHaveAttribute("aria-checked", "true");
    expect((screen.getByRole("spinbutton", { name: /depth/i }) as HTMLInputElement).value).toBe("3");
  });

  it("invokes onCancel from the Cancel affordance", () => {
    const { onCancel } = renderForm();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
