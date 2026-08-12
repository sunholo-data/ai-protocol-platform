interface EmptyTabProps {
  title: string;
  body: string;
}

/**
 * Contextual empty state for a Workbench tab (v6.4.0 INTERNAL-SHELL M2).
 *
 * Used by Workbench when a tab's `content` is null and an `emptyBody` is
 * provided. Renders a centered title + body instead of a blank panel so
 * the user always knows what to do next ("No X yet — pick from sidebar").
 *
 * Ported from gde-ap-agent app/chat/[...path]/page.tsx lines 424-432
 * (defined inline; generic component, no AP-specific dependencies).
 */
export function EmptyTab({ title, body }: EmptyTabProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 py-16 text-center">
      <h3 className="text-lg font-semibold tracking-tight text-foreground">
        {title}
      </h3>
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
        {body}
      </p>
    </div>
  );
}
