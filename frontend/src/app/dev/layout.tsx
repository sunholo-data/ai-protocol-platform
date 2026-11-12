// Developer smoke pages (A2UI, MCP Apps, rich media, file browser). They render
// fixture data and are for `next dev` only: a production build answers 404.
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

export default function DevLayout({ children }: { children: ReactNode }) {
  if (process.env.NODE_ENV === "production") notFound();
  return <>{children}</>;
}
