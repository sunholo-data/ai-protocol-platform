import { redirect } from "next/navigation";

/** A bare section URL goes to your chats rather than a 404. */
export default function Page() {
  redirect("/");
}
