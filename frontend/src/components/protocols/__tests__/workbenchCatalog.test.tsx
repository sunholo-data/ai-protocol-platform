// The workbench catalog, built for real — no SDK mock (see a2uiMarkdownCatalog.test).

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { basicCatalog } from "@a2ui/react/v0_9";
import { ButtonContent, workbenchCatalog } from "../workbenchCatalog";

describe("workbenchCatalog", () => {
  it("keeps basicCatalog's id and every component it has", () => {
    expect(workbenchCatalog.id).toBe(basicCatalog.id);
    expect([...workbenchCatalog.components.keys()].sort()).toEqual([...basicCatalog.components.keys()].sort());
  });

  it("replaces Button, Text and Modal and leaves the others alone", () => {
    for (const name of ["Button", "Text", "Modal"]) {
      expect(workbenchCatalog.components.get(name)).not.toBe(basicCatalog.components.get(name));
    }
    expect(workbenchCatalog.components.get("Card")).toBe(basicCatalog.components.get("Card"));
  });
});

describe("ButtonContent", () => {
  it("exposes the variant the SDK's own Button loses (2026-10-28)", () => {
    const { getByRole } = render(<ButtonContent variant="primary">Approve once</ButtonContent>);
    expect(getByRole("button", { name: "Approve once" })).toHaveAttribute("data-variant", "primary");
  });

  it("names the default variant rather than leaving it absent", () => {
    const { getByRole } = render(<ButtonContent>Decline</ButtonContent>);
    expect(getByRole("button")).toHaveAttribute("data-variant", "default");
  });

  it("fires its action and honours disabled", () => {
    const onClick = vi.fn();
    const { getByRole, rerender } = render(<ButtonContent onClick={onClick}>Refresh</ButtonContent>);
    fireEvent.click(getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<ButtonContent onClick={onClick} disabled>Refresh</ButtonContent>);
    fireEvent.click(getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(getByRole("button")).toBeDisabled();
  });
});
