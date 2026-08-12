"use client";

import { type ReactNode, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { AccessRestrictedScreen } from "@/components/auth/AccessRestrictedScreen";
import { DOMAIN_NOT_PERMITTED_EVENT } from "@/lib/apiClient";

/**
 * App-wide gate that swaps the whole UI for {@link AccessRestrictedScreen} the
 * moment any authenticated call reports the caller's email domain isn't
 * permitted (v6.18.0 Gap B). `fetchWithAuth` dispatches
 * {@link DOMAIN_NOT_PERMITTED_EVENT} on that backend 403; because every authed
 * request flows through `fetchWithAuth`, whichever call the app makes first
 * after sign-in trips this — no per-caller wiring.
 *
 * Mounted inside AuthProvider so it can read the signed-in email and drive
 * sign-out. Inert for LOCAL_MODE / anonymous-group auth (the backend exempts
 * those, so the event never fires).
 */
export function AccessRestrictedGate({ children }: { children: ReactNode }) {
  const { user, signOut } = useAuth();
  const [denied, setDenied] = useState<{ message?: string } | null>(null);

  useEffect(() => {
    const onDenied = (e: Event) => {
      const message = (e as CustomEvent<{ message?: string }>).detail?.message;
      setDenied({ message });
    };
    window.addEventListener(DOMAIN_NOT_PERMITTED_EVENT, onDenied);
    return () => window.removeEventListener(DOMAIN_NOT_PERMITTED_EVENT, onDenied);
  }, []);

  // Once the rejected user signs out (user → null), drop the overlay so the
  // normal sign-in screen shows through instead of a stuck restricted screen.
  useEffect(() => {
    if (!user) setDenied(null);
  }, [user]);

  if (denied) {
    return (
      <AccessRestrictedScreen
        email={user?.email ?? null}
        message={denied.message}
        onSignOut={signOut}
      />
    );
  }
  return <>{children}</>;
}
