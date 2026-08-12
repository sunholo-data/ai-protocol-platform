import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Direct proxy to the sidecar backend /health endpoint.
 *
 * There is also a generic catch-all at `../[...path]/route.ts` (reinstated in
 * AUTH-PERMISSIONS M4). This explicit route takes precedence (Next prefers
 * the more specific match) and keeps `/health` blindingly simple — no header
 * filtering, no auth forwarding, no request-body handling — so it's clear
 * at a glance that the health probe never carries an auth token.
 */
export async function GET() {
  // Sidecar backend listens on 1956 (see backend/Dockerfile). Use 127.0.0.1,
  // not "localhost" — Node's DNS can resolve localhost to ::1 (IPv6) while
  // uvicorn binds 0.0.0.0 (IPv4 only), causing silent fetch-failed. Also: do
  // NOT use :8080 — that's the frontend's own ingress port, which loops back
  // into this Next process and returns its 404 page.
  // G20: `||` not `??` (Cloud Run sets every declared var; absent → "").
  const backend = process.env.BACKEND_URL || "http://127.0.0.1:1956";
  try {
    const upstream = await fetch(`${backend}/health`, { cache: "no-store" });
    const body = await upstream.text();
    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: "backend_unreachable", message: String(err) },
      { status: 502 },
    );
  }
}
