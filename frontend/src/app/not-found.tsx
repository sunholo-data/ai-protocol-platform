import Link from "next/link";
import { MapPinOff } from "lucide-react";
import { AppHeader } from "@/components/navigation/AppHeader";

/** Any address the app doesn't have: the normal header and two ways on. */
export default function NotFound() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <AppHeader />
      <main className="grid flex-1 place-items-center px-4 text-center">
        <div>
          <MapPinOff className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden />
          <h1 className="mt-3 text-lg font-semibold text-foreground">There&rsquo;s nothing at this address</h1>
          <p className="mt-1 text-sm text-muted-foreground">It may have moved, or the link is incomplete.</p>
          <div className="mt-5 flex justify-center gap-2">
            <Link
              href="/"
              className="rounded-[var(--radius)] bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Go to your chats
            </Link>
            <Link
              href="/help"
              className="rounded-[var(--radius)] border border-border px-4 py-2 text-sm hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Help
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
