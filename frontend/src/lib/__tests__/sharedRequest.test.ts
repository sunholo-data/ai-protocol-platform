import { afterEach, describe, expect, it, vi } from "vitest";
import { forgetSharedRequests, sharedRequest } from "@/lib/sharedRequest";

afterEach(() => {
  vi.useRealTimers();
  forgetSharedRequests();
});

describe("sharedRequest", () => {
  it("callers within the window share one load", async () => {
    const load = vi.fn().mockResolvedValue(1);
    const [a, b] = await Promise.all([sharedRequest("k", load, 1000), sharedRequest("k", load, 1000)]);
    expect(a).toBe(1);
    expect(b).toBe(1);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("loads again once the window has passed", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValue(1);
    await sharedRequest("k", load, 1000);
    vi.advanceTimersByTime(1001);
    await sharedRequest("k", load, 1000);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("forgets a failure at once so the next caller retries", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(2);
    await expect(sharedRequest("k", load, 60_000)).rejects.toThrow("boom");
    await expect(sharedRequest("k", load, 60_000)).resolves.toBe(2);
  });
});
