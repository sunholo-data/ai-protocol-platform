// Tests for DefinitionList — the label/value layout primitive that solves
// the cramped-label problem in inline JSON cards (G29).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DefinitionList } from "../DefinitionList";

describe("DefinitionList", () => {
  it("renders nothing when items list is empty", () => {
    const { container } = render(<DefinitionList items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders each item as a <dt>/<dd> pair", () => {
    render(
      <DefinitionList
        items={[
          { label: "Vendor", value: "Acme GmbH" },
          { label: "Total", value: "1,234.56" },
        ]}
      />,
    );
    expect(screen.getByText("Vendor").tagName).toBe("DT");
    expect(screen.getByText("Acme GmbH").tagName).toBe("DD");
    expect(screen.getByText("Total").tagName).toBe("DT");
    expect(screen.getByText("1,234.56").tagName).toBe("DD");
  });

  it("uses the fixed-width label-column grid template (G29 contract)", () => {
    const { container } = render(
      <DefinitionList items={[{ label: "Vendor", value: "Acme" }]} />,
    );
    const dl = container.querySelector("dl") as HTMLDListElement;
    // The whole point of this primitive: labels never wrap because the
    // grid pins them to a fixed minmax(120px,160px) column.
    expect(dl.className).toContain("grid-cols-[minmax(120px,160px)_minmax(0,1fr)]");
  });

  it("applies tabular-nums + right-align when numeric=true", () => {
    const { container } = render(
      <DefinitionList
        numeric
        items={[{ label: "Total", value: "1234.56" }]}
      />,
    );
    const dd = container.querySelector("dd") as HTMLElement;
    expect(dd.className).toContain("font-mono");
    expect(dd.className).toContain("tabular-nums");
    expect(dd.className).toContain("text-right");
  });

  it("does NOT apply numeric styling when numeric is unset", () => {
    const { container } = render(
      <DefinitionList items={[{ label: "Vendor", value: "Acme" }]} />,
    );
    const dd = container.querySelector("dd") as HTMLElement;
    expect(dd.className).not.toContain("font-mono");
    expect(dd.className).not.toContain("tabular-nums");
  });

  it("uses dense gap when tone='dense'", () => {
    const { container } = render(
      <DefinitionList
        tone="dense"
        items={[{ label: "Vendor", value: "Acme" }]}
      />,
    );
    const dl = container.querySelector("dl") as HTMLDListElement;
    expect(dl.className).toContain("gap-y-1");
    expect(dl.className).not.toContain("gap-y-2");
  });

  it("uses comfortable gap by default", () => {
    const { container } = render(
      <DefinitionList items={[{ label: "Vendor", value: "Acme" }]} />,
    );
    const dl = container.querySelector("dl") as HTMLDListElement;
    expect(dl.className).toContain("gap-y-2");
  });

  it("applies per-row valueClassName override without disturbing the rest", () => {
    const { container } = render(
      <DefinitionList
        items={[
          { label: "Status", value: "INVALID", valueClassName: "text-red-500" },
          { label: "Vendor", value: "Acme" },
        ]}
      />,
    );
    const dds = container.querySelectorAll("dd");
    expect(dds[0].className).toContain("text-red-500");
    expect(dds[1].className).not.toContain("text-red-500");
  });

  it("accepts ReactNode values, not just strings", () => {
    render(
      <DefinitionList
        items={[
          {
            label: "Action",
            value: <button type="button">Approve</button>,
          },
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });
});
