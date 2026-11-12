"use client";

import { Button } from "@/components/ui/button";

interface ReadOnlyComposerProps {
  onContinue: () => void;
}

/**
 * Shown instead of the normal message composer when the user is viewing
 * a session they do not own (read-only mode). The "Continue from here"
 * button opens a new session — fork endpoint is deferred to v6.1.
 */
export default function ReadOnlyComposer({ onContinue }: ReadOnlyComposerProps) {
  return (
    <div className="flex items-center gap-3 border-t bg-muted p-4">
      <p className="flex-1 text-sm text-muted-foreground">You&apos;re viewing someone else&apos;s conversation.</p>
      <Button variant="primary" onClick={onContinue}>
        Continue in a new chat
      </Button>
    </div>
  );
}
