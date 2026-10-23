// app/error.tsx + app/global-error.tsx — FRONTEND-OBS M1
//
// These are Next App Router file conventions, so they are never imported by
// application code — which is exactly why they are worth a test. A typo in the
// props contract does not fail a build; it fails the first time a customer hits
// an error, which is the worst possible moment to discover it.
//
// Version contract (Next 15.5): the recovery prop is `reset`. `retry` is Next
// 16.2 (unstable) / 16.3 (stable) and does NOT exist for us — a component
// written against `retry` renders a dead button here.

import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ErrorPage from "../error";
import GlobalError from "../global-error";
import { GlobalErrorBody } from "../_components/GlobalErrorBody";

// Both pages report through the app-wide logger (M3). Mock it here for two
// reasons: to assert the wiring, and so this file can never reach the real
// fetchWithAuth → Firebase path — the singleton is inert under test anyway,
// but the 2026-09-04 CI hang is the reason that guard exists, and a test that
// depends on it would silently become the next one.
const loggerError = vi.hoisted(() => vi.fn());
vi.mock("@/lib/logger", () => ({ logger: { error: loggerError, warn: vi.fn(), event: vi.fn() } }));

beforeEach(() => {
  loggerError.mockClear();
});

describe("app/error.tsx", () => {
  it("shows a visible notice rather than an empty segment", () => {
    render(<ErrorPage error={new Error("boom")} reset={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/something went wrong/i);
  });

  it("calls `reset` — the Next 15.5 prop — when the user retries", async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<ErrorPage error={new Error("boom")} reset={reset} />);
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("surfaces the digest so a user can quote it and we can find the server log", () => {
    const error = Object.assign(new Error("boom"), { digest: "abc123def" });
    render(<ErrorPage error={error} reset={vi.fn()} />);
    expect(screen.getByText(/abc123def/)).toBeInTheDocument();
  });

  it("reports the error to the logger with its digest (the M3 wiring)", () => {
    const error = Object.assign(new Error("boom"), { digest: "abc123def" });
    render(<ErrorPage error={error} reset={vi.fn()} />);
    expect(loggerError).toHaveBeenCalledWith(error, expect.objectContaining({ digest: "abc123def" }));
  });

  it("omits the digest block entirely when there is none (client-side error)", () => {
    render(<ErrorPage error={new Error("boom")} reset={vi.fn()} />);
    expect(screen.queryByTestId("error-digest")).not.toBeInTheDocument();
  });

  it("does not print the raw error message — it can quote document content", () => {
    // The fixture stands in for the real hazard: a thrown message that quotes
    // the payload that caused it, which here is a customer's document.
    render(
      <ErrorPage
        error={new Error("clause 7.2: counterparty pays 42 per unit")}
        reset={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("42 per unit");
  });
});

describe("app/global-error.tsx", () => {
  // The wrapper renders <html>/<body>, which must NEVER go through RTL: in
  // vitest's CI mode every file shares one jsdom document, and a React 19
  // <html> render into it does not recover (the 2026-09-04 ci-gate hang).
  // Structure is asserted on static markup; interaction on the body component.

  it("renders its own <html> and <body> — it replaces the root layout", () => {
    const html = renderToStaticMarkup(<GlobalError error={new Error("boom")} reset={vi.fn()} />);
    expect(html).toContain("<html");
    expect(html).toContain("<body");
  });

  it("styles itself inline — global stylesheets do not reach global-error", () => {
    const html = renderToStaticMarkup(<GlobalError error={new Error("boom")} reset={vi.fn()} />);
    expect(html).toMatch(/<body[^>]+style="/);
    expect(html).not.toMatch(/className=/);
  });

  it("calls `reset` when retried", async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<GlobalErrorBody error={new Error("boom")} reset={reset} />);
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("surfaces the digest when there is one", () => {
    const error = Object.assign(new Error("boom"), { digest: "xyz789" });
    render(<GlobalErrorBody error={error} reset={vi.fn()} />);
    expect(screen.getByTestId("global-error-digest")).toHaveTextContent("xyz789");
  });

  it("does not print the raw error message", () => {
    render(<GlobalErrorBody error={new Error("clause 7.2: counterparty pays 42 per unit")} reset={vi.fn()} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("42 per unit");
  });
});
