"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SIDEBAR_DEFAULT, SIDEBAR_MAX, SIDEBAR_MIN } from "@/hooks/useSidebarWidth";

const KEY_STEP = 16;

interface SidebarResizeHandleProps {
  width: number;
  onChange: (px: number) => void;
}

/**
 * Drag handle on the right edge of the chat page's left sidebar. Renders right
 * after the sidebar `<aside>` and measures from that element's left edge.
 * Keyboard: Tab to it, ←/→ by 16px, Home/End to min/max. Double-click (or
 * Enter) resets the default width. Hidden on phones, where the sidebar is a sheet.
 */
export function SidebarResizeHandle({ width, onChange }: SidebarResizeHandleProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const leftRef = useRef(0);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => onChange(e.clientX - leftRef.current);
    const onUp = () => setDragging(false);
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [dragging, onChange]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const aside = ref.current?.previousElementSibling as HTMLElement | null;
    leftRef.current = aside?.getBoundingClientRect().left ?? 0;
    setDragging(true);
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const next =
        e.key === "ArrowLeft" ? width - KEY_STEP
        : e.key === "ArrowRight" ? width + KEY_STEP
        : e.key === "Home" ? SIDEBAR_MIN
        : e.key === "End" ? SIDEBAR_MAX
        : e.key === "Enter" ? SIDEBAR_DEFAULT
        : null;
      if (next === null) return;
      e.preventDefault();
      onChange(next);
    },
    [width, onChange],
  );

  return (
    <div
      ref={ref}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={SIDEBAR_MAX}
      aria-valuenow={width}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown}
      onDoubleClick={() => onChange(SIDEBAR_DEFAULT)}
      onKeyDown={onKeyDown}
      data-testid="sidebar-resize-handle"
      className={`group relative z-10 -ml-1 hidden w-2 shrink-0 cursor-col-resize touch-none focus-visible:outline-none md:block`}
    >
      <span
        aria-hidden="true"
        className={`absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 transition-colors group-hover:bg-primary/40 group-focus-visible:bg-primary ${dragging ? "bg-primary" : "bg-transparent"}`}
      />
    </div>
  );
}
