import type { ReactNode } from "react";

/** One labelled setting: what it does on the left, the control on the right. */
export function SettingRow({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-foreground">{title}</p>
        {note ? <div className="mt-0.5 text-xs text-muted-foreground">{note}</div> : null}
      </div>
      {children}
    </div>
  );
}
