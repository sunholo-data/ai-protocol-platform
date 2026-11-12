import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlatformWorkStatus } from "../PlatformWorkStatus";

describe("platform work status", () => {
  it("explains pending separately from started work and keeps the worker scope clear", () => {
    const { rerender } = render(<PlatformWorkStatus status="pending" stageLabel="Loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Pending — waiting for the platform");
    expect(screen.queryByText("Loading")).not.toBeInTheDocument();
    rerender(<PlatformWorkStatus status="running" stageLabel="Searching the web" />);
    expect(screen.getByRole("status")).toHaveTextContent("Started — platform skill is working");
    expect(screen.getByText("Searching the web")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("another agent needs that agent's status");
  });

  it("does not imply an idle or resumed view is running", () => {
    render(<PlatformWorkStatus status="idle" />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
