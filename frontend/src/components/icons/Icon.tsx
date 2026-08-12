import type { ReactNode, SVGProps } from "react";

/**
 * Aitana icon primitive.
 *
 * Every custom Aitana glyph renders through this wrapper so the whole set is
 * visually consistent — the "Aitana style": 24×24 grid, 1.75 stroke, round
 * caps/joins, `currentColor` (so `text-*` classes colour it), no fill.
 *
 * This matches the existing lucide-react look (which we keep for standard UI
 * glyphs), so custom brand icons sit alongside lucide ones without clashing.
 * See ./README.md for the full spec.
 */
export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "ref"> {
  /** Pixel size for width & height. Default 20 (h-5 w-5 equivalent). */
  size?: number | string;
}

export function Icon({
  size = 20,
  className,
  children,
  ...props
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      {...props}
    >
      {children}
    </svg>
  );
}
