import { cn } from "@/lib/utils";

const SIZES = { xs: "h-3 w-3", sm: "h-4 w-4", md: "h-5 w-5", lg: "h-8 w-8" } as const;

/**
 * The one loading spinner. Use it inside buttons and chips; content areas get
 * a `Skeleton` instead. Decorative by default — pair it with visible text, or
 * pass `label` to announce it.
 */
export function Spinner({
  size = "sm",
  label,
  className,
}: {
  size?: keyof typeof SIZES;
  label?: string;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn("shrink-0 animate-spin", SIZES[size], className)}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
