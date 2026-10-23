import Link from "next/link";

import { skillHref } from "@/components/navigation/skillHref";
import { BRANDING } from "@/lib/branding";
import { PLATFORM_OWNER_UID } from "@/lib/platformOwner";
import { readManifest } from "@/lib/userGuide";
import { withAppName } from "@/lib/userGuideParse";

export default function HelpIndex() {
  const { pages } = readManifest();
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">Using {BRANDING.appName}</h1>
        <p className="text-muted-foreground">Step-by-step guides with screenshots. Pick a topic to start.</p>
        <p>
          <Link
            href={skillHref({ skillId: "platform-guide", slug: "platform-guide", ownerId: PLATFORM_OWNER_UID })}
            className="inline-flex items-center rounded-full border border-border px-3 py-1 text-sm text-foreground hover:bg-muted"
          >
            Ask about {BRANDING.appName} →
          </Link>
        </p>
      </header>
      {pages.length === 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          The user guide hasn&apos;t been bundled into this build. Run <code>node scripts/user-guide/bundle.mjs</code>.
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {pages.map((p) => (
            <li key={p.slug}>
              <Link
                href={`/help/${p.slug}`}
                className="block h-full space-y-1 rounded-lg border border-border p-4 hover:bg-muted"
              >
                <span className="block font-semibold text-foreground">{withAppName(p.title, BRANDING.appName)}</span>
                <span className="block text-sm text-muted-foreground">{withAppName(p.summary, BRANDING.appName)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
