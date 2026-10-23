import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * The build id of the frontend actually being served. A tab compares it with
 * the id baked into its own bundle (`NEXT_PUBLIC_BUILD_ID`, see
 * next.config.mjs) — a mismatch means a newer build has deployed since the
 * page loaded. See `components/AppUpdater.tsx`.
 */
export function GET() {
  return NextResponse.json(
    { buildId: process.env.NEXT_PUBLIC_BUILD_ID || "unknown" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
