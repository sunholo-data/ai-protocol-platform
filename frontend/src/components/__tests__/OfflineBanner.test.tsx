import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OfflineBanner } from "../OfflineBanner";

describe("OfflineBanner", () => {
  it("is silent online, speaks up offline, and confirms the reconnect", () => {
    render(<OfflineBanner />);
    expect(screen.queryByRole("status")).toBeNull();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByRole("status").textContent).toMatch(/offline/i);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.getByRole("status").textContent).toBe("Back online.");
  });
});
