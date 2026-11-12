import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button } from "../button";
import { ConfirmProvider, useConfirm } from "../confirm";
import { Toaster, notify } from "../toast";
import { useAsyncAction } from "@/hooks/useAsyncAction";

afterEach(() => {
  vi.useRealTimers();
});

describe("Button", () => {
  it("disables itself and says what it is doing while pending", () => {
    render(
      <Button pending pendingLabel="Saving…">
        Save
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute("aria-busy")).toBe("true");
  });
});

describe("useConfirm", () => {
  function Harness({ onResult }: { onResult: (ok: boolean) => void }) {
    const confirm = useConfirm();
    return (
      <button type="button" onClick={async () => onResult(await confirm({ title: "Delete it?", confirmLabel: "Delete" }))}>
        open
      </button>
    );
  }

  it("resolves true on confirm and false on cancel", async () => {
    const results: boolean[] = [];
    render(
      <ConfirmProvider>
        <Harness onResult={(ok) => results.push(ok)} />
      </ConfirmProvider>,
    );
    await userEvent.click(screen.getByText("open"));
    expect(await screen.findByRole("alertdialog", { name: "Delete it?" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByText("open"));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(results).toEqual([true, false]));
  });

  it("falls back to window.confirm outside the provider", async () => {
    const spy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const results: boolean[] = [];
    render(<Harness onResult={(ok) => results.push(ok)} />);
    await userEvent.click(screen.getByText("open"));
    await waitFor(() => expect(results).toEqual([true]));
    expect(spy).toHaveBeenCalledWith("Delete it?");
    spy.mockRestore();
  });
});

describe("notify", () => {
  it("shows a toast, runs its action, and dismisses itself", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const undo = vi.fn();
    render(<Toaster />);
    act(() => {
      notify("Conversation deleted", { action: { label: "Undo", onClick: undo } });
      notify.error("Couldn't save");
    });
    expect(screen.getByText("Conversation deleted")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Couldn't save");
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledOnce();
    expect(screen.queryByText("Conversation deleted")).toBeNull();
    // The pointer is still on the stack, so the error waits for it to leave.
    const stack = screen.getByText("Couldn't save").closest("[aria-live]") as HTMLElement;
    act(() => {
      vi.advanceTimersByTime(9000);
    });
    expect(screen.getByText("Couldn't save")).toBeTruthy();
    await userEvent.unhover(stack);
    act(() => {
      vi.advanceTimersByTime(9000);
    });
    expect(screen.queryByText("Couldn't save")).toBeNull();
    vi.useRealTimers();
  });
});

describe("useAsyncAction", () => {
  it("tracks pending, ignores double submits, and reports failure", async () => {
    let finish: (v: string) => void = () => {};
    const fn = vi.fn(() => new Promise<string>((r) => (finish = r)));
    const { result } = renderHook(() => useAsyncAction(fn, { error: "" }));
    let first: Promise<string | undefined> = Promise.resolve(undefined);
    act(() => {
      first = result.current.run();
      void result.current.run();
    });
    expect(result.current.pending).toBe(true);
    expect(fn).toHaveBeenCalledOnce();
    await act(async () => {
      finish("done");
      expect(await first).toBe("done");
    });
    expect(result.current.pending).toBe(false);

    const failing = renderHook(() =>
      useAsyncAction(async () => {
        throw new Error("Nope");
      }, { error: (e) => (e as Error).message }),
    );
    await act(async () => {
      expect(await failing.result.current.run()).toBeUndefined();
    });
    expect(failing.result.current.error).toBe("Nope");
  });
});
