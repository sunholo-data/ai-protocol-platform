"use client";

import { useCallback, useRef, useState } from "react";
import { notify } from "@/components/ui/toast";

export interface AsyncActionOptions<T> {
  /** Toast on success, e.g. "Saved". A function gets the result. */
  success?: string | ((result: T) => string);
  /** Toast on failure. Defaults to the error's own message. */
  error?: string | ((err: unknown) => string);
}

/**
 * pending → done → error for any async action, with no double submits.
 *
 *   const save = useAsyncAction(() => api.save(draft), { success: "Saved" });
 *   <Button pending={save.pending} onClick={() => void save.run()}>Save</Button>
 *
 * `run` never throws: it resolves to the result, or `undefined` on failure
 * (the error is in `error` and, unless `error: ""`, in a toast).
 */
export function useAsyncAction<A extends unknown[], T>(
  fn: (...args: A) => Promise<T>,
  opts: AsyncActionOptions<T> = {},
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const run = useCallback(async (...args: A): Promise<T | undefined> => {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await fnRef.current(...args);
      const { success } = optsRef.current;
      if (success) notify.success(typeof success === "function" ? success(result) : success);
      return result;
    } catch (err) {
      const { error: errOpt } = optsRef.current;
      const message =
        typeof errOpt === "function"
          ? errOpt(err)
          : errOpt ?? (err instanceof Error && err.message ? err.message : "Something went wrong. Try again.");
      setError(message);
      if (message) notify.error(message);
      return undefined;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }, []);

  const reset = useCallback(() => setError(null), []);
  return { run, pending, error, reset };
}
