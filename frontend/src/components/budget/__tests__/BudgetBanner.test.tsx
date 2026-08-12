import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BudgetBanner } from "../BudgetBanner";
import type { StreamError } from "@/hooks/useSkillAgent";

function makeBudgetError(overrides: Partial<StreamError> = {}): StreamError {
  return {
    kind: "budget_exceeded",
    message: "Cohort PHYS-7K2N is over its monthly budget.",
    retryable: true,
    rawMessage: "Cohort PHYS-7K2N is over its monthly budget.",
    retryAfterSeconds: 3600,
    ...overrides,
  };
}

describe("BudgetBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when error is null", () => {
    const { container } = render(<BudgetBanner error={null} onDismiss={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when error is a non-budget kind", () => {
    const err: StreamError = {
      kind: "run_error",
      message: "Agent run failed",
      retryable: true,
      rawMessage: "Agent run failed",
    };
    const { container } = render(<BudgetBanner error={err} onDismiss={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the backend message verbatim — no client-side interpolation", () => {
    render(<BudgetBanner error={makeBudgetError()} onDismiss={() => {}} />);
    expect(screen.getByText("Cohort PHYS-7K2N is over its monthly budget.")).toBeInTheDocument();
  });

  it("uses role='alert' + aria-live='assertive' so hard-block is a state the user must notice", () => {
    render(<BudgetBanner error={makeBudgetError()} onDismiss={() => {}} />);
    const banner = screen.getByTestId("budget-banner");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveAttribute("aria-live", "assertive");
  });

  it("renders the retry-after countdown", () => {
    render(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 3600 })} onDismiss={() => {}} />);
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("Resets in 1 hour.");
  });

  it("formats days / hours / minutes / seconds correctly", () => {
    // days
    const { rerender } = render(
      <BudgetBanner error={makeBudgetError({ retryAfterSeconds: 86400 * 3 })} onDismiss={() => {}} />,
    );
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("3 days");
    // hours
    rerender(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 7200 })} onDismiss={() => {}} />);
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("2 hours");
    // minutes
    rerender(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 600 })} onDismiss={() => {}} />);
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("10 minutes");
    // seconds
    rerender(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 30 })} onDismiss={() => {}} />);
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("30 seconds");
  });

  it("countdown ticks down once per second", () => {
    render(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 3 })} onDismiss={() => {}} />);
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("3 seconds");
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("2 seconds");
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByTestId("budget-countdown")).toHaveTextContent("1 second");
  });

  it("hides countdown when it reaches zero", () => {
    render(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: 1 })} onDismiss={() => {}} />);
    act(() => vi.advanceTimersByTime(1500));
    expect(screen.queryByTestId("budget-countdown")).not.toBeInTheDocument();
  });

  it("hides countdown when retryAfterSeconds is absent (enforcer didn't project)", () => {
    render(<BudgetBanner error={makeBudgetError({ retryAfterSeconds: undefined })} onDismiss={() => {}} />);
    expect(screen.queryByTestId("budget-countdown")).not.toBeInTheDocument();
  });

  it("calls onDismiss when the 'Got it' button is clicked", () => {
    const onDismiss = vi.fn();
    render(<BudgetBanner error={makeBudgetError()} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByTestId("budget-banner-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("dismiss button is keyboard-focusable", () => {
    render(<BudgetBanner error={makeBudgetError()} onDismiss={() => {}} />);
    const btn = screen.getByTestId("budget-banner-dismiss");
    btn.focus();
    expect(btn).toHaveFocus();
  });
});
