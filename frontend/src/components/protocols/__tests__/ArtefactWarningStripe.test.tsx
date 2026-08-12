import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtefactWarningStripe } from "../ArtefactWarningStripe";

describe("ArtefactWarningStripe", () => {
  it("renders the message and the reason code chip", () => {
    render(
      <ArtefactWarningStripe message="Review pending" reasonCode="REVIEW_PENDING">
        <div data-testid="child">child</div>
      </ArtefactWarningStripe>,
    );
    expect(screen.getByTestId("artefact-warning-stripe")).toHaveTextContent("Review pending");
    expect(screen.getByTestId("artefact-warning-reason")).toHaveTextContent("REVIEW_PENDING");
  });

  it("renders the wrapped child below the stripe", () => {
    render(
      <ArtefactWarningStripe message="ok" reasonCode="OK">
        <div data-testid="wrapped-artefact">artefact body</div>
      </ArtefactWarningStripe>,
    );
    expect(screen.getByTestId("wrapped-artefact")).toBeInTheDocument();
  });

  it("uses role='status' + aria-live='polite' (informational, doesn't interrupt)", () => {
    render(
      <ArtefactWarningStripe message="msg" reasonCode="RC">
        <div />
      </ArtefactWarningStripe>,
    );
    const stripe = screen.getByTestId("artefact-warning-stripe");
    expect(stripe).toHaveAttribute("role", "status");
    expect(stripe).toHaveAttribute("aria-live", "polite");
  });
});
