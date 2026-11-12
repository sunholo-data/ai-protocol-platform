"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Button } from "./button";

export interface ConfirmOptions {
  title: string;
  /** One short sentence on what happens. */
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red confirm button, for deletes and revokes. */
  destructive?: boolean;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * `const confirm = useConfirm(); if (!(await confirm({ title: "Delete?" }))) return;`
 * — a drop-in for `window.confirm` that looks like the app, traps focus and
 * closes on Esc. Outside the provider (tests, isolated renders) it falls back
 * to `window.confirm`.
 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  return (
    ctx ??
    (async (opts) =>
      typeof window === "undefined" ? false : window.confirm([opts.title, opts.description].filter(Boolean).join("\n\n")))
  );
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((next) => {
    resolver.current?.(false);
    setOpts(next);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const settle = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setOpts(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <AlertDialog.Root open={opts !== null} onOpenChange={(open) => !open && settle(false)}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-[90] bg-black/40 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0" />
          <AlertDialog.Content className="fixed left-1/2 top-1/2 z-[91] w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 space-y-4 rounded-md border bg-popover p-5 text-popover-foreground shadow-lg motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95">
            <div className="space-y-1.5">
              <AlertDialog.Title className="text-base font-semibold">{opts?.title}</AlertDialog.Title>
              {opts?.description ? (
                <AlertDialog.Description className="text-sm text-muted-foreground">
                  {opts.description}
                </AlertDialog.Description>
              ) : (
                <AlertDialog.Description className="sr-only">Confirm this action.</AlertDialog.Description>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <Button variant="secondary">{opts?.cancelLabel ?? "Cancel"}</Button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <Button variant={opts?.destructive ? "destructive" : "primary"} onClick={() => settle(true)}>
                  {opts?.confirmLabel ?? "Confirm"}
                </Button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </ConfirmContext.Provider>
  );
}
