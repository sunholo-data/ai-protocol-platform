import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { PageFrame } from "@/components/navigation/AppHeader";
import { isLocalModeBannerEnabled } from "@/lib/localMode";

// The workshop guide is for people running the code locally: the LOCAL_MODE
// banner is its only way in. A deployment without that banner answers 404.
export default function Layout({ children }: { children: ReactNode }) {
  if (!isLocalModeBannerEnabled()) notFound();
  return <PageFrame>{children}</PageFrame>;
}
