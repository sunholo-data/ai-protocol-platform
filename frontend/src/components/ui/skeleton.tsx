import { cn } from "@/lib/utils";

/** A placeholder block for content that is loading. Size it with classes. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("rounded-md bg-muted motion-safe:animate-pulse", className)} />;
}

/** A page-shaped placeholder: a title, a line of text and a few rows. */
export function PageSkeleton({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-4">
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="space-y-2 pt-2">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </div>
  );
}

/** Whole-page loading state for the pages outside chat. */
export function PageLoading() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 md:px-6">
      <PageSkeleton />
    </div>
  );
}

/**
 * First paint of the chat: the header, a sidebar on wide screens, an empty
 * transcript and the composer — so the app arrives in the shape it will have,
 * instead of a blank page with "Loading…" in the corner.
 */
export function ChatSkeleton() {
  return (
    <div role="status" aria-label="Loading your workspace" className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-16 shrink-0 items-center gap-3 border-b border-border px-4">
        <Skeleton className="h-9 w-9 rounded-full" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-9 w-44" />
        <div className="flex-1" />
        <Skeleton className="h-9 w-9 rounded-full" />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="hidden w-64 shrink-0 space-y-3 border-r border-border p-4 lg:block">
          <Skeleton className="h-4 w-24" />
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
        <div className="flex min-w-0 flex-1 flex-col items-center justify-end p-6">
          <Skeleton className="h-24 w-full max-w-2xl" />
        </div>
      </div>
    </div>
  );
}
