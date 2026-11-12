"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { authImagePath, useAuthImage } from "@/lib/authImage";

interface InlineImageProps {
  src: string;
  alt?: string;
}

function BrokenImageFallback({ alt }: { alt?: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border bg-muted px-2 py-1 text-xs text-muted-foreground">
      {/* broken image icon */}
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
        <line x1="1" y1="1" x2="23" y2="23" />
      </svg>
      {alt ?? "image unavailable"}
    </span>
  );
}

/**
 * An image, video or audio clip in a chat reply. A backend `/api/...` source
 * (a document, bucket object or artefact the agent showed with `show_media`) is
 * fetched with the user's credentials first, since a media element cannot send
 * them, and plays by its type; anything else loads directly as an image.
 */
export function InlineImage({ src, alt }: InlineImageProps) {
  const [errored, setErrored] = useState(false);
  const [open, setOpen] = useState(false);
  const authPath = authImagePath(src);
  const auth = useAuthImage(authPath);

  if (errored || (authPath && auth.failed)) return <BrokenImageFallback alt={alt} />;
  if (authPath && !auth.url) {
    return (
      <span
        role="img"
        aria-label={alt ? `Loading ${alt}` : "Loading media"}
        aria-busy="true"
        className="my-2 block h-48 w-full max-w-sm animate-pulse rounded border border-border bg-muted"
      />
    );
  }
  if (authPath && auth.type.startsWith("video/")) {
    return (
      <video
        src={auth.url as string}
        controls
        preload="metadata"
        aria-label={alt ?? "Video"}
        className="my-2 block max-w-full rounded border border-border"
        style={{ maxHeight: "400px" }}
        onError={() => setErrored(true)}
      />
    );
  }
  if (authPath && auth.type.startsWith("audio/")) {
    return (
      <span className="my-2 block">
        {alt ? <span className="mb-1 block text-xs text-muted-foreground">{alt}</span> : null}
        <audio
          src={auth.url as string}
          controls
          preload="metadata"
          aria-label={alt ?? "Audio"}
          className="w-full max-w-md"
          onError={() => setErrored(true)}
        />
      </span>
    );
  }
  const shown = authPath ? (auth.url as string) : src;

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={shown}
          alt={alt ?? ""}
          loading="lazy"
          decoding="async"
          className="my-2 max-w-full cursor-zoom-in rounded border border-border object-contain"
          style={{ maxHeight: "400px" }}
          onError={() => setErrored(true)}
        />
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">{alt ?? "Image preview"}</Dialog.Title>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={shown}
            alt={alt ?? ""}
            className="max-h-[90vh] max-w-[90vw] rounded object-contain shadow-2xl"
          />
          <Dialog.Close
            className="absolute -right-3 -top-3 flex h-6 w-6 items-center justify-center rounded-full bg-white text-black shadow-md hover:bg-muted"
            aria-label="Close"
          >
            ×
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
