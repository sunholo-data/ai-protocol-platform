"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/**
 * Lets a shell body hand controls up to the shared header (ShellChrome →
 * SkillsBar) without prop-drilling through ShellRouter. Today: the sidebar
 * toggle. ChatShell owns the sidebar state and registers it; the header shows
 * the button only while a body has registered one, so shells without a
 * sidebar show nothing.
 */
export interface SidebarToggle {
  open: boolean;
  toggle: () => void;
}

/** Skill-level account actions the header owns, so a body's menu sheet can
 * offer them on phones (where the header hides its own buttons). */
export interface AccountActions {
  onConfigure?: () => void;
  onCreate: () => void;
}

interface ShellActions {
  sidebar: SidebarToggle | null;
  setSidebar: (s: SidebarToggle | null) => void;
  account: AccountActions | null;
}

const ShellActionsContext = createContext<ShellActions | null>(null);

export function ShellActionsProvider({
  children,
  account = null,
}: {
  children: ReactNode;
  account?: AccountActions | null;
}) {
  const [sidebar, setSidebar] = useState<SidebarToggle | null>(null);
  return (
    <ShellActionsContext.Provider value={{ sidebar, setSidebar, account }}>{children}</ShellActionsContext.Provider>
  );
}

/** The header's account actions, or null outside a ShellChrome. */
export function useAccountActions(): AccountActions | null {
  return useContext(ShellActionsContext)?.account ?? null;
}

/** For the header: the registered sidebar toggle, or null. */
export function useSidebarToggle(): SidebarToggle | null {
  return useContext(ShellActionsContext)?.sidebar ?? null;
}

/**
 * For a shell body: register (and keep current) the sidebar toggle. A no-op
 * outside a provider, so bodies rendered standalone (tests) still work.
 * `toggle` must be stable (useCallback).
 */
export function useRegisterSidebarToggle(open: boolean, toggle: () => void): void {
  const setSidebar = useContext(ShellActionsContext)?.setSidebar;
  useEffect(() => {
    if (!setSidebar) return;
    setSidebar({ open, toggle });
  }, [setSidebar, open, toggle]);
  useEffect(() => {
    if (!setSidebar) return;
    return () => setSidebar(null);
  }, [setSidebar]);
}
