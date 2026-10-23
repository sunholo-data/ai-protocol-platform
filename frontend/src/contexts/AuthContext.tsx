"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  AnonymousGroupAuthProvider,
  useAnonymousGroupAuth,
} from "@/contexts/AnonymousGroupAuthProvider";
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
import {
  getIdToken as fbGetIdToken,
  signInWithGoogle as fbSignInWithGoogle,
  signInWithGoogleRedirect as fbSignInWithGoogleRedirect,
  signOut as fbSignOut,
  subscribeToAuthState,
  type User,
} from "@/lib/firebase";
import {
  isLocalMode,
  LOCAL_MODE_STUB_TOKEN,
  LOCAL_MODE_WORKSHOP_USER,
} from "@/lib/localMode";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  getIdToken: () => Promise<string | null>;
  signIn: () => Promise<void>;
  signInWithRedirect: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Build a Firebase-User-shaped stub for LOCAL_MODE. Consumers reading
 * `uid` / `email` / `displayName` get what they expect; methods on the
 * real SDK aren't present (which is correct — LOCAL_MODE has no SDK). */
function buildLocalModeStubUser(): User {
  return {
    uid: LOCAL_MODE_WORKSHOP_USER.uid,
    email: LOCAL_MODE_WORKSHOP_USER.email,
    displayName: LOCAL_MODE_WORKSHOP_USER.displayName,
    photoURL: LOCAL_MODE_WORKSHOP_USER.photoURL,
  } as unknown as User;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Anonymous group-ID mode (sprint 2.11) — checked FIRST so forks
  // can opt into it without also unsetting NEXT_PUBLIC_LOCAL_MODE in
  // their deployment. Wraps in AnonymousGroupAuthProvider, then
  // adapts the group-auth state to the platform's AuthContext shape.
  if (isAnonymousGroupAuthMode()) {
    return (
      <AnonymousGroupAuthProvider>
        <AnonymousGroupAuthAdapter>{children}</AnonymousGroupAuthAdapter>
      </AnonymousGroupAuthProvider>
    );
  }

  if (isLocalMode()) {
    return <LocalModeAuthProvider>{children}</LocalModeAuthProvider>;
  }

  return <FirebaseAuthProvider>{children}</FirebaseAuthProvider>;
}

/** How long to wait for /whoami before settling on the stub identity. */
const LOCAL_WHOAMI_TIMEOUT_MS = 2000;

/**
 * LOCAL_MODE auth — no Firebase, no sign-in screen. The stub token is the
 * credential, but WHO the backend treats the caller as depends on the host:
 * a bare workshop backend answers with the workshop user, while a
 * tailnet-served deployment (AUTH_MODE=tailnet) answers with the real
 * Tailscale login. Asking /api/auth/whoami once keeps the UI in step with
 * the backend — the right name in the account menu, and the right uid for
 * "Your skills" / your files — instead of every tailnet member seeing
 * "workshop@local". Falls back to the stub if the call fails or is slow.
 */
function LocalModeAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let settled = false;
    const settle = (u: User) => {
      if (settled) return;
      settled = true;
      setUser(u);
    };
    const timer = setTimeout(() => settle(buildLocalModeStubUser()), LOCAL_WHOAMI_TIMEOUT_MS);
    fetch("/api/proxy/api/auth/whoami", {
      headers: { Authorization: `Bearer ${LOCAL_MODE_STUB_TOKEN}` },
      cache: "no-store",
    })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((me: { uid?: string; email?: string; displayName?: string }) => {
        if (!me.uid || me.uid === LOCAL_MODE_WORKSHOP_USER.uid) {
          settle(buildLocalModeStubUser());
          return;
        }
        settle({
          uid: me.uid,
          email: me.email || null,
          displayName: me.displayName || null,
          photoURL: null,
        } as unknown as User);
      })
      .catch(() => settle(buildLocalModeStubUser()));
    return () => {
      settled = true;
      clearTimeout(timer);
    };
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading: user === null,
        getIdToken: async () => LOCAL_MODE_STUB_TOKEN,
        signIn: async () => {},
        signInWithRedirect: async () => {},
        signOut: async () => {},
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

/** Bridge AnonymousGroupAuth state to the main AuthContext shape so
 * the rest of the app (e.g. `useAuth().user`) doesn't need to branch
 * on auth mode. signIn/signOut are no-ops here — students reach the
 * sign-in state via the `/group` page, not via a button. */
function AnonymousGroupAuthAdapter({ children }: { children: ReactNode }) {
  const group = useAnonymousGroupAuth();
  const user: User | null = group.user
    ? ({
        uid: group.user.uid,
        email: group.user.email,
        displayName: group.user.displayName,
        photoURL: group.user.photoURL,
      } as unknown as User)
    : null;
  return (
    <AuthContext.Provider
      value={{
        user,
        loading: false,
        getIdToken: async () => group.token,
        // No interactive sign-in — the /group page handles join.
        signIn: async () => {},
        signInWithRedirect: async () => {},
        // Sign-out drops the sessionStorage token and returns to /group.
        signOut: async () => {
          group.clearStoredToken();
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

function FirebaseAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = subscribeToAuthState((nextUser) => {
      setUser(nextUser);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        getIdToken: fbGetIdToken,
        signIn: fbSignInWithGoogle,
        signInWithRedirect: fbSignInWithGoogleRedirect,
        signOut: fbSignOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
