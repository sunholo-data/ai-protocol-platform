// Tool-call chip — compact spinner/check/error indicator with the tool's
// friendly label (CLAUDE.md #9). The raw tool name is secondary detail only:
// the chip's `title` and the hover card's header.
// MCP App UI surfaces are handled by MCPAppToolCallRouter (mounted by
// MessageBubble) using the tool DEFINITION's _meta.ui binding, per the
// MCP Apps spec (UI-by-reference). The chip always renders just a chip;
// it does NOT inspect resultContent for ui:// URIs anymore.
// See: docs/design/v6.1.0/mcp-app-integrations.md (M2A — frontend)
//
// Hovering the chip opens a card with the same "Called with" / "Result"
// detail the Workbench Activity tab shows, so the user can peek at what a
// tool is doing without leaving the chat. Hover-only by design: no click
// behaviour, and the card closes when the pointer leaves chip and card.

"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import type { ToolCallState } from "@/hooks/useSkillAgent";
import { ToolCallDetails, hasToolDetail } from "@/components/activity/ToolCallDetails";
import { FLOATING_SURFACE } from "@/lib/surfaces";
import { useToolLabels } from "@/hooks/useToolLabels";

const MAX_TOOL_LABEL = 32;

interface ToolCallChipProps {
  toolCall: ToolCallState;
}

export function ToolCallChip({ toolCall }: ToolCallChipProps) {
  const labelFor = useToolLabels();
  const fullLabel = labelFor(toolCall.name);
  const label =
    fullLabel.length > MAX_TOOL_LABEL ? fullLabel.slice(0, MAX_TOOL_LABEL) + "…" : fullLabel;

  return (
    <Tooltip.Provider delayDuration={300} skipDelayDuration={100}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <div
            tabIndex={0}
            data-testid="tool-call-chip"
            data-tool-name={toolCall.name}
            title={toolCall.name}
            className="inline-flex cursor-default items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-muted-foreground outline-none hover:border-foreground/20 focus-visible:ring-2 focus-visible:ring-ring"
          >
            {toolCall.status === "running" && <Spinner />}
            {toolCall.status === "success" && <CheckIcon />}
            {toolCall.status === "error" && <ErrorIcon />}
            <span>{label}</span>
          </div>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            side="top"
            align="start"
            sideOffset={6}
            collisionPadding={12}
            data-testid="tool-call-chip-detail"
            className={`${FLOATING_SURFACE} flex max-h-[min(24rem,var(--radix-tooltip-content-available-height))] w-[min(28rem,calc(100vw-2rem))] flex-col overflow-hidden duration-150 data-[state=delayed-open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0`}
          >
            <ChipDetail toolCall={toolCall} label={fullLabel} />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

const STATUS_LABEL: Record<ToolCallState["status"], string> = {
  running: "Running…",
  success: "Done",
  error: "Failed",
};

function ChipDetail({ toolCall, label }: { toolCall: ToolCallState; label: string }) {
  const hasDetail = hasToolDetail(toolCall.argsJson, toolCall.resultContent);
  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <span className="min-w-0 flex-1 truncate">
          <span className="font-medium text-foreground/80">{label}</span>
          {label !== toolCall.name && (
            <span data-testid="tool-call-raw-name" className="ml-1.5 font-mono text-[10px] text-muted-foreground/70">
              {toolCall.name}
            </span>
          )}
        </span>
        <span
          className={
            toolCall.status === "error"
              ? "shrink-0 text-red-600 dark:text-red-400"
              : "shrink-0 text-muted-foreground"
          }
        >
          {STATUS_LABEL[toolCall.status]}
        </span>
      </div>
      <div className="min-h-0 overflow-auto p-3">
        {hasDetail ? (
          <ToolCallDetails argsJson={toolCall.argsJson} resultContent={toolCall.resultContent} />
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {toolCall.status === "running" ? "Waiting for the tool's input…" : "No details recorded for this call."}
          </p>
        )}
      </div>
    </>
  );
}

function Spinner() {
  return (
    <svg
      className="h-3 w-3 animate-spin text-orange-500"
      viewBox="0 0 24 24"
      fill="none"
      aria-label="Running"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      className="h-3 w-3 text-green-600"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-label="Success"
    >
      <path d="M3 8l3.5 3.5L13 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg
      className="h-3 w-3 text-red-600"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-label="Error"
    >
      <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
    </svg>
  );
}
