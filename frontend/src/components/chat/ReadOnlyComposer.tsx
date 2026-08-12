"use client";

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
    <div className="border-t border-gray-200 bg-gray-50 p-4 flex items-center gap-3">
      <p className="flex-1 text-sm text-gray-500">
        You are viewing a shared conversation. Your messages will not be sent.
      </p>
      <button
        onClick={onContinue}
        className="shrink-0 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
      >
        Continue from here as new session
      </button>
    </div>
  );
}
