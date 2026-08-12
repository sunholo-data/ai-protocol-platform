import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "platform-frontend",
    // G20: `||` (not `??`) so an empty COMMIT_SHA falls back to "unknown".
    commit: process.env.COMMIT_SHA || "unknown",
  });
}
