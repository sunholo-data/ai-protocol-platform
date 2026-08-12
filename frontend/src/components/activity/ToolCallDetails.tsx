// Shared expanded tool-call detail: "Called with" (args as a key→value list)
// and "Result" (JSON tree or clean text), each copyable. The chat Activity tab
// and the admin analytics timeline render the exact same detail body.

"use client";

import { Fragment } from "react";
import { cn } from "@/lib/utils";
import { Section } from "@/components/activity/bits";
import { JsonTree } from "@/components/activity/JsonTree";
import { displayValue, formatResult, parseArgs } from "@/components/activity/format";

export function ToolCallDetails({ argsJson, resultContent }: { argsJson?: string; resultContent?: string }) {
  const argsView = parseArgs(argsJson);
  const resultView = formatResult(resultContent);
  if (!argsView && !resultView) return null;

  return (
    <div className="flex flex-col gap-2">
      {argsView && (
        <Section
          label="Called with"
          copyText={
            argsView.fields
              ? JSON.stringify(Object.fromEntries(argsView.fields), null, 2)
              : argsView.raw
          }
        >
          {argsView.fields ? (
            <dl className="grid grid-cols-[minmax(0,8rem)_1fr] gap-x-3 gap-y-1">
              {argsView.fields.map(([k, v]) => {
                const dv = displayValue(v);
                return (
                  <Fragment key={k}>
                    <dt className="truncate font-mono text-[10px] text-muted-foreground/80" title={k}>
                      {k}
                    </dt>
                    <dd
                      className={cn(
                        "min-w-0 break-words text-[11px] text-foreground/80",
                        dv.mono && "whitespace-pre-wrap font-mono",
                      )}
                    >
                      {dv.text}
                    </dd>
                  </Fragment>
                );
              })}
            </dl>
          ) : (
            <pre className="whitespace-pre-wrap break-words text-[11px] text-foreground/70">{argsView.raw}</pre>
          )}
        </Section>
      )}
      {resultView && (
        <Section label="Result" copyText={resultView.copyText}>
          {resultView.kind === "json" ? (
            <JsonTree value={resultView.value} />
          ) : (
            <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-foreground/80">
              {resultView.text}
            </p>
          )}
        </Section>
      )}
    </div>
  );
}

/** True when a tool call has any expandable detail (args or result). */
export function hasToolDetail(argsJson?: string, resultContent?: string): boolean {
  return Boolean(parseArgs(argsJson)) || Boolean(formatResult(resultContent));
}
