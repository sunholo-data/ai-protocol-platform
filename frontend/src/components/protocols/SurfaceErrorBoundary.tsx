// SurfaceErrorBoundary — FRONTEND-OBS M1
//
// Contains a render throw to the tenant surface.
//
// Until this existed there was no error boundary anywhere in the app — no
// `app/error.tsx`, no `app/global-error.tsx`, no class boundary in the tree.
// A malformed A2UI payload that threw inside `<A2uiSurface>` therefore
// unmounted the whole React subtree with no notice to the user and no record
// anywhere. That is exactly the failure CLAUDE.md principle #8 (NEVER SILENT)
// names, and it is the visible half of the recurring "A2UI won't render in the
// Workspace" bug class: the agent narrates "I've updated the Workspace", the
// tab stays on its empty state, and nothing says why.
//
// ISOLATION is the point. A boundary high in the tree would catch the throw
// and still blank the workspace, which is no better for the user. This one
// wraps each surface individually, so a bad surface degrades to an in-place
// notice while its siblings and the chat keep working.
//
// WHAT THIS DOES NOT CATCH — read before assuming it covers you. React error
// boundaries see errors thrown during *render*, in lifecycle methods, and in
// constructors below them. They do NOT see:
//   - errors in event handlers (a click handler that throws)
//   - async errors (a rejected promise, a setTimeout callback)
//   - errors thrown in the boundary itself
//   - server-side render errors (Next's error.tsx / global-error.tsx handle those)
// The other half is FRONTEND-OBS M3's `window.onerror` + `unhandledrejection`
// handlers. Neither half is sufficient alone.

"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

export interface SurfaceErrorBoundaryProps {
  /** Friendly surface name shown in the notice — tells the user WHICH thing broke. */
  surfaceId: string;
  children: ReactNode;
  /**
   * Reported on every caught error. FRONTEND-OBS M3 wires this to
   * `logger.error` so the componentStack reaches Cloud Logging; until then the
   * boundary is still useful, it just reports nowhere.
   */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface SurfaceErrorBoundaryState {
  /** Bumped on retry to force a fresh subtree rather than a reused failed one. */
  attempt: number;
  hasError: boolean;
}

export class SurfaceErrorBoundary extends Component<
  SurfaceErrorBoundaryProps,
  SurfaceErrorBoundaryState
> {
  state: SurfaceErrorBoundaryState = { attempt: 0, hasError: false };

  static getDerivedStateFromError(): Partial<SurfaceErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Deliberately not swallowed: React's own console.error still fires, and
    // onError forwards the error AND the componentStack, which is the part
    // that makes a client-side stack trace worth shipping at all.
    this.props.onError?.(error, info);
  }

  private handleRetry = () => {
    // `attempt` is part of the child key, so a retry remounts the subtree from
    // scratch. Clearing `hasError` alone would re-render the same failed
    // element and, if it throws again, React would tear down the boundary too.
    this.setState((prev) => ({ attempt: prev.attempt + 1, hasError: false }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          data-testid="surface-error"
          className="my-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          <p className="font-medium">This surface failed to render.</p>
          <p className="mt-1 text-xs opacity-90">
            {/*
              The surface id, not the error message. A thrown message can quote
              the payload that caused it, and an A2UI payload carries customer
              content — a contract clause in a red box on screen is a leak with
              extra steps. The detail belongs in the log (M3), not the DOM.
            */}
            Surface <code className="font-mono">{this.props.surfaceId}</code> could not be
            displayed. The rest of this page still works.
          </p>
          <button
            type="button"
            onClick={this.handleRetry}
            className="mt-2 rounded border border-destructive/40 px-2 py-1 text-xs font-medium hover:bg-destructive/10"
          >
            Try again
          </button>
        </div>
      );
    }

    return <div key={this.state.attempt}>{this.props.children}</div>;
  }
}
