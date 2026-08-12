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

  // LOCAL_MODE: mount immediately with a deterministic stub identity.
  // No Firebase listeners, no loading flicker, no sign-in screen.
  if (isLocalMode()) {
    return (
      <AuthContext.Provider
        value={{
          user: buildLocalModeStubUser(),
          loading: false,
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

  return <FirebaseAuthProvider>{children}</FirebaseAuthProvider>;
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
