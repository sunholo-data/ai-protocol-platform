// v6.18.0 Gap B — fetchWithAuth must dispatch DOMAIN_NOT_PERMITTED_EVENT when
// (and only when) the backend rejects the caller's email domain, so a top-level
// gate can render the "access restricted" screen (NEVER-SILENT #8).

import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/firebase", () => ({
  getIdToken: vi.fn(async () => "fake-token"),
}));

import { DOMAIN_NOT_PERMITTED_EVENT, fetchWithAuth } from "@/lib/apiClient";

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
  );
}

/** Resolve when the rejection event fires, or reject after a short timeout. */
function waitForDeniedEvent(timeoutMs = 1000): Promise<CustomEvent<{ message?: string }>> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("no domain-rejection event")), timeoutMs);
    window.addEventListener(
      DOMAIN_NOT_PERMITTED_EVENT,
      (e) => {
        clearTimeout(timer);
        resolve(e as CustomEvent<{ message?: string }>);
      },
      { once: true },
    );
  });
}

const macrotask = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchWithAuth domain-rejection signal", () => {
  it("dispatches the event on a DOMAIN_NOT_PERMITTED 403", async () => {
    mockFetch(403, { detail: { code: "DOMAIN_NOT_PERMITTED", message: "nope" } });
    const eventP = waitForDeniedEvent();
    const res = await fetchWithAuth("/api/proxy/api/auth/whoami");
    const ev = await eventP;
    expect(ev.detail?.message).toBe("nope");
    // The caller's response body is still readable (we parsed a clone).
    expect((await res.json()).detail.code).toBe("DOMAIN_NOT_PERMITTED");
  });

  it("does NOT dispatch on an unrelated 403", async () => {
    mockFetch(403, { detail: "Only the bucket owner can update" });
    let fired = 0;
    const h = () => {
      fired += 1;
    };
    window.addEventListener(DOMAIN_NOT_PERMITTED_EVENT, h);
    await fetchWithAuth("/api/proxy/api/buckets/x");
    await macrotask();
    window.removeEventListener(DOMAIN_NOT_PERMITTED_EVENT, h);
    expect(fired).toBe(0);
  });

  it("does NOT dispatch on a 200", async () => {
    mockFetch(200, { ok: true });
    let fired = 0;
    const h = () => {
      fired += 1;
    };
    window.addEventListener(DOMAIN_NOT_PERMITTED_EVENT, h);
    await fetchWithAuth("/api/proxy/api/skills/marketplace");
    await macrotask();
    window.removeEventListener(DOMAIN_NOT_PERMITTED_EVENT, h);
    expect(fired).toBe(0);
  });
});
