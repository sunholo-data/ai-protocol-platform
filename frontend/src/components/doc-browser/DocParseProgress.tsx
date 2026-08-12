"use client";

interface DocParseProgressProps {
  parsedCount: number;
  failedCount: number;
  docCount: number;
}

export function DocParseProgress({ parsedCount, failedCount, docCount }: DocParseProgressProps) {
  const pendingCount = docCount - parsedCount - failedCount;
  if (docCount === 0 || (pendingCount <= 0 && failedCount === 0)) return null;

  const pct = Math.round((parsedCount / docCount) * 100);

  return (
    <div className="border-t px-3 py-2 space-y-1">
      {pendingCount > 0 && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">
              Parsing: {parsedCount} / {docCount - failedCount} complete
            </span>
            <span className="text-[10px] font-medium text-foreground">{pct}%</span>
          </div>
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-teal-500 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </>
      )}
      {failedCount > 0 && (
        <p className="text-[10px] text-destructive">
          {failedCount} file{failedCount !== 1 ? "s" : ""} failed to parse — hover for details
        </p>
      )}
    </div>
  );
}
