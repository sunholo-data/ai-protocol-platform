// SurfaceErrorBoundary — FRONTEND-OBS M1
//
// The gap this closes: a malformed A2UI payload that throws while rendering
// `<A2uiSurface>` currently unmounts the whole React subtree with no notice
// and no record. That is CLAUDE.md principle #8's named anti-pattern, and it
// is the visible symptom of the recurring "A2UI won't render in the Workspace"
// bug class — the user is told the Workspace updated, and sees nothing.
//
// The criterion that matters most here is ISOLATION: a boundary that catches
// the throw but still takes the rest of the workspace down with it has not
// solved the problem. Hence the sibling test.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SurfaceErrorBoundary } from "../SurfaceErrorBoundary";

// React logs every boundary-caught error to console.error. That is correct
// behaviour and we do not want to suppress it in the app — only to keep the
// test output readable.
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function Boom({ message = "malformed A2UI payload" }: { message?: string }): never {
  throw new Error(message);
}

describe("SurfaceErrorBoundary", () => {
  it("renders children when nothing throws", () => {
    render(
      <SurfaceErrorBoundary surfaceId="workspace">
        <div>healthy surface</div>
      </SurfaceErrorBoundary>,
    );
    expect(screen.getByText("healthy surface")).toBeInTheDocument();
    expect(screen.queryByTestId("surface-error")).not.toBeInTheDocument();
  });

  it("catches a render throw and shows a visible notice instead of a blank panel", () => {
    render(
      <SurfaceErrorBoundary surfaceId="workspace">
        <Boom />
      </SurfaceErrorBoundary>,
    );
    const notice = screen.getByTestId("surface-error");
    expect(notice).toBeInTheDocument();
    // NEVER SILENT: it must be announced, not just painted.
    expect(notice).toHaveAttribute("role", "alert");
    expect(notice).toHaveTextContent(/failed to render/i);
  });

  it("names the surface, so the notice says WHICH thing broke", () => {
    render(
      <SurfaceErrorBoundary surfaceId="ppa-compare">
        <Boom />
      </SurfaceErrorBoundary>,
    );
    expect(screen.getByTestId("surface-error")).toHaveTextContent("ppa-compare");
  });

  it("ISOLATION: a throwing surface does not take its siblings down", () => {
    render(
      <div>
        <SurfaceErrorBoundary surfaceId="broken">
          <Boom />
        </SurfaceErrorBoundary>
        <SurfaceErrorBoundary surfaceId="healthy">
          <div>sibling still here</div>
        </SurfaceErrorBoundary>
        <div>chat still usable</div>
      </div>,
    );
    expect(screen.getByTestId("surface-error")).toBeInTheDocument();
    expect(screen.getByText("sibling still here")).toBeInTheDocument();
    expect(screen.getByText("chat still usable")).toBeInTheDocument();
  });

  it("recovers when the user retries and the child no longer throws", async () => {
    const user = userEvent.setup();
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error("transient");
      return <div>recovered surface</div>;
    }

    render(
      <SurfaceErrorBoundary surfaceId="workspace">
        <Flaky />
      </SurfaceErrorBoundary>,
    );
    expect(screen.getByTestId("surface-error")).toBeInTheDocument();

    shouldThrow = false;
    await user.click(screen.getByRole("button", { name: /try again/i }));

    expect(screen.getByText("recovered surface")).toBeInTheDocument();
    expect(screen.queryByTestId("surface-error")).not.toBeInTheDocument();
  });

  it("stays in the error state when a retry throws again", async () => {
    const user = userEvent.setup();
    render(
      <SurfaceErrorBoundary surfaceId="workspace">
        <Boom />
      </SurfaceErrorBoundary>,
    );
    await user.click(screen.getByRole("button", { name: /try again/i }));
    // Still broken — and still saying so, rather than flickering to blank.
    expect(screen.getByTestId("surface-error")).toBeInTheDocument();
  });

  it("reports the error to onError, which is M3's hook for the logger", () => {
    const onError = vi.fn();
    render(
      <SurfaceErrorBoundary surfaceId="workspace" onError={onError}>
        <Boom message="specific failure" />
      </SurfaceErrorBoundary>,
    );
    expect(onError).toHaveBeenCalledTimes(1);
    const [error, info] = onError.mock.calls[0];
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("specific failure");
    // componentStack is what makes a client stack trace worth shipping.
    expect(info).toHaveProperty("componentStack");
  });

  it("does not leak the raw error message into the UI", () => {
    // An A2UI payload can carry customer document content; a thrown message can
    // quote it. The notice is for the user, the detail goes to the log (M3).
    render(
      <SurfaceErrorBoundary surfaceId="workspace">
        <Boom message="clause 7.2: counterparty pays 42 per unit" />
      </SurfaceErrorBoundary>,
    );
    expect(screen.getByTestId("surface-error")).not.toHaveTextContent("42 per unit");
  });
});
