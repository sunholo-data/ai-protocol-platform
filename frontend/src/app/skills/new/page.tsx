// v6.6.0 ONE-FORK-CONVERGENCE M3: the CLI-pointer placeholder is replaced by a
// thin redirect into the Skill Studio create flow.

"use client";

import { redirect } from "next/navigation";

export default function NewSkillPage() {
  redirect("/skills/studio/new");
}
