import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StreamErrorBanner } from "@/components/chat/StreamErrorBanner";

const noop = () => {};

describe("StreamErrorBanner", () => {
  it("offers Reload conversation for a dropped connection", () => {
    const onReload = vi.fn();
    render(
      <StreamErrorBanner
        error={{ kind: "network", message: "The connection dropped.", retryable: true, rawMessage: "x" }}
        onRetry={noop}
        onDismiss={noop}
        onReload={onReload}
      />,
    );
    expect(screen.getByText(/reply may still arrive/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reload conversation" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("does not offer it for other errors, or without a conversation to reload", () => {
    const { rerender } = render(
      <StreamErrorBanner
        error={{ kind: "run_error", message: "Agent error.", retryable: true, rawMessage: "x" }}
        onRetry={noop}
        onDismiss={noop}
        onReload={noop}
      />,
    );
    expect(screen.queryByRole("button", { name: "Reload conversation" })).toBeNull();
    rerender(
      <StreamErrorBanner
        error={{ kind: "network", message: "Dropped.", retryable: true, rawMessage: "x" }}
        onRetry={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.queryByRole("button", { name: "Reload conversation" })).toBeNull();
  });
});
