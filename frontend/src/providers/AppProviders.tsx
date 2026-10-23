"use client";

import { useEffect, type ReactNode } from "react";
import { AccessRestrictedGate } from "@/components/auth/AccessRestrictedGate";
import { AuthProvider } from "@/contexts/AuthContext";
import { installGlobalHandlers } from "@/lib/logger";

/**
 * Composite provider tree. PHASE1-UI will add A2UI + CopilotKit providers here.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  // FRONTEND-OBS M3. React error boundaries (M1) only see errors thrown during
  // render. Event-handler throws and unhandled promise rejections — which is
  // most of what actually breaks in a streaming chat app — reach neither a
  // boundary nor any log. These handlers are that other half; the flush on tab
  // hide is registered here too, so a user closing the tab mid-failure still
  // gets their events off the machine.
  useEffect(() => installGlobalHandlers(), []);

  return (
    <AuthProvider>
      <AccessRestrictedGate>{children}</AccessRestrictedGate>
    </AuthProvider>
  );
}
