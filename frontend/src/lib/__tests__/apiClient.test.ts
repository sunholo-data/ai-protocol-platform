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
function waitForDeniedEvent(timeoutMs = 1000): Promise<CustomEvent<{ message?: string; code?: string }>> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("no domain-rejection event")), timeoutMs);
    window.addEventListener(
      DOMAIN_NOT_PERMITTED_EVENT,
      (e) => {
        clearTimeout(timer);
        resolve(e as CustomEvent<{ message?: string; code?: string }>);
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

  it("dispatches the event, with its code, on an EMAIL_NOT_VERIFIED 403 (unverified-email gate)", async () => {
    mockFetch(403, { detail: { code: "EMAIL_NOT_VERIFIED", message: "unverified" } });
    const eventP = waitForDeniedEvent();
    await fetchWithAuth("/api/proxy/api/auth/whoami");
    const ev = await eventP;
    expect(ev.detail?.code).toBe("EMAIL_NOT_VERIFIED");
    expect(ev.detail?.message).toBe("unverified");
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

describe("fetchWithAuth response timeout", () => {
  function hangingFetch() {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: unknown, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            if (init?.signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      ),
    );
  }

  it("rejects with a TimeoutError when the server never starts responding", async () => {
    hangingFetch();
    await expect(fetchWithAuth("/api/proxy/api/slow", {}, { timeoutMs: 20 })).rejects.toMatchObject({
      name: "TimeoutError",
    });
  });

  it("still reports the caller's own abort as an abort, not a timeout", async () => {
    hangingFetch();
    const ac = new AbortController();
    const p = fetchWithAuth("/api/proxy/api/slow", { signal: ac.signal }, { timeoutMs: 5000 });
    ac.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });

  it("does not time out a response that has already started", async () => {
    mockFetch(200, { ok: true });
    const res = await fetchWithAuth("/api/proxy/api/fast", {}, { timeoutMs: 20 });
    await new Promise((r) => setTimeout(r, 40));
    expect((await res.json()).ok).toBe(true);
  });

  it("sets no default timeout on uploads", async () => {
    mockFetch(200, {});
    const fd = new FormData();
    await fetchWithAuth("/api/proxy/api/upload", { method: "POST", body: fd });
    const init = (fetch as unknown as { mock: { calls: [unknown, RequestInit][] } }).mock.calls[0][1];
    expect(init.signal).toBeUndefined();
  });
});
