"use client";

import { useState, type ReactNode } from "react";

// WorkspaceShell — split-pane container for workbench skills
// (v6.4.0 ONE-DEMO M3, ported inline from fork-visual-demo-pullback 4.1 M1).
//
// Left pane: chat. Right pane: workbench artefact (SideBySideDocViewer
// for one-doc-compare, or any future workbench). The middle divider is
// resizable via WorkspaceDivider.
//
// Sized via CSS grid with a CSS variable for the left column width,
// driven by an internal useState. Keeps the layout-engine simple and
// avoids the layout thrash patterns that plagued the v5 split-pane.

interface WorkspaceShellProps {
  chat: ReactNode;
  workbench: ReactNode;
  /** Initial chat-pane width as a fraction (0..1). Default 0.4. */
  initialChatFraction?: number;
  /** Minimum chat-pane fraction. Default 0.2. */
  minChatFraction?: number;
  /** Maximum chat-pane fraction. Default 0.7. */
  maxChatFraction?: number;
  /** Test/probe hook — receives the current fraction whenever it changes. */
  onFractionChange?: (fraction: number) => void;
}

export function WorkspaceShell({
  chat,
  workbench,
  initialChatFraction = 0.4,
  minChatFraction = 0.2,
  maxChatFraction = 0.7,
  onFractionChange,
}: WorkspaceShellProps) {
  const clampedInitial = Math.max(
    minChatFraction,
    Math.min(maxChatFraction, initialChatFraction),
  );
  const [chatFraction, setChatFraction] = useState(clampedInitial);

  function updateFraction(next: number) {
    const clamped = Math.max(minChatFraction, Math.min(maxChatFraction, next));
    setChatFraction(clamped);
    onFractionChange?.(clamped);
  }

  return (
    <div
      data-testid="workspace-shell"
      className="grid h-full w-full overflow-hidden"
      style={{
        gridTemplateColumns: `${chatFraction * 100}% 8px 1fr`,
      }}
    >
      <div data-testid="workspace-chat-pane" className="min-w-0 overflow-hidden">
        {chat}
      </div>
      <WorkspaceDividerHandle
        currentFraction={chatFraction}
        onFractionChange={updateFraction}
        minFraction={minChatFraction}
        maxFraction={maxChatFraction}
      />
      <div data-testid="workspace-workbench-pane" className="min-w-0 overflow-hidden">
        {workbench}
      </div>
    </div>
  );
}

// Internal divider — exposed as a separate `WorkspaceDivider` component
// in its own file for direct import use cases. This in-shell version is
// pre-wired to the shell's fraction state. Kept inline so adoption of
// the shell doesn't force an additional import.
function WorkspaceDividerHandle({
  currentFraction,
  onFractionChange,
  minFraction,
  maxFraction,
}: {
  currentFraction: number;
  onFractionChange: (fraction: number) => void;
  minFraction: number;
  maxFraction: number;
}) {
  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const containerRect = event.currentTarget.parentElement?.getBoundingClientRect();
    if (!containerRect) return;
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);

    function onMove(moveEvent: PointerEvent) {
      if (!containerRect) return;
      const offsetX = moveEvent.clientX - containerRect.left;
      const fraction = offsetX / containerRect.width;
      onFractionChange(fraction);
    }
    function onUp(upEvent: PointerEvent) {
      target.releasePointerCapture(upEvent.pointerId);
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
    }

    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
  }

  return (
    <div
      data-testid="workspace-divider"
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={minFraction}
      aria-valuemax={maxFraction}
      aria-valuenow={currentFraction}
      onPointerDown={onPointerDown}
      className="group flex h-full cursor-col-resize items-center justify-center bg-gray-100 hover:bg-gray-200"
    >
      <div className="h-8 w-0.5 rounded bg-gray-300 group-hover:bg-gray-400" />
    </div>
  );
}
