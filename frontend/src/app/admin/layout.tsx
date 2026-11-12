import type { ReactNode } from "react";
import { PageFrame } from "@/components/navigation/AppHeader";

export default function Layout({ children }: { children: ReactNode }) {
  return <PageFrame>{children}</PageFrame>;
}
