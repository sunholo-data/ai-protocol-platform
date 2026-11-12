import { redirect } from "next/navigation";

// Settings is one page now; this address goes to its section.
export default function Page() {
  redirect("/settings#instructions");
}
