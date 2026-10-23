"use client";

import { useRouter } from "next/navigation";

/**
 * `useRouter()` for components that also render outside the App Router (unit
 * tests, embedded panels): returns null instead of throwing "expected app
 * router to be mounted". The hook call itself is unconditional, so hook order
 * is stable.
 */
export function useOptionalRouter(): ReturnType<typeof useRouter> | null {
  try {
    return useRouter();
  } catch {
    return null;
  }
}

/** Navigate with the router when there is one, else a full page load. */
export function navigateTo(router: ReturnType<typeof useRouter> | null, href: string): void {
  if (router) router.push(href);
  else if (typeof window !== "undefined") window.location.assign(href);
}
