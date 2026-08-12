"use client";

/**
 * Full-screen "access restricted" panel for a signed-in user whose email
 * domain isn't permitted on this deployment (v6.18.0 Gap B — the backend
 * `DOMAIN_NOT_PERMITTED` 403). Firebase sign-in succeeds before the backend
 * rejects them, so this screen is what stops them landing "signed in" on a
 * broken/empty app (NEVER-SILENT #8). It only offers a way OUT — sign out and
 * switch to a permitted account; there is no retry that could succeed on the
 * same identity.
 *
 * Visual language mirrors SkillNotFound / SignInRequired (centered column,
 * mono uppercase eyebrow, semibold headline, signed-in-as box, sign-out button)
 * so the wrong-account family of screens reads consistently.
 */
export function AccessRestrictedScreen({
  email,
  message,
  onSignOut,
}: {
  email: string | null;
  message?: string | null;
  onSignOut: () => Promise<void> | void;
}) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="max-w-lg space-y-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Access restricted
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground md:text-3xl">
          This workspace is limited to your organization&apos;s accounts.
        </h1>
        <p className="text-sm text-muted-foreground">
          {message?.trim()
            ? message
            : "Your account isn't permitted on this deployment. Sign in with your work account, or ask an administrator to enable access for your organization."}
        </p>
        <div className="mt-4 rounded-md border border-border bg-muted/30 p-3 text-left text-xs text-muted-foreground">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider opacity-70">
              Signed in as
            </span>
            <span className="font-mono text-foreground">{email ?? "(unknown account)"}</span>
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={() => void onSignOut()}
        className="rounded-md border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted"
      >
        Sign out and switch account
      </button>
    </main>
  );
}
