// Per-message agent attribution (6.11) — which agent produced a given bot
// message, so the transcript can show that agent's avatar/name and the mark
// changes as the conversation hands off (ONE Assistant → Web Researcher → …).
//
// Model: each turn is `user message → (optional auto delegation) → assistant
// reply`. A delegation anchors at the turn's USER message (`afterMessageId`),
// and its delegate produces the messages AFTER that anchor. So the agent for a
// message at position P is the latest AUTO delegation whose anchor sits before P
// (the most recent handoff that had happened by the time this message was
// produced). No applicable delegation → the root skill answered directly.

export interface AgentAttribution {
  /** Avatar to show on the bubble; null → fall back to the brand mark. */
  avatar: string | null;
  /** Display name for the bubble header; null → the root skill name. */
  label: string | null;
}

export interface AgentDelegation {
  afterMessageId: string | null;
  targetDisplay: string;
  avatar: string | null;
  mode: "auto" | "suggest";
}

/**
 * Resolve the producing agent for the message with `messageId`.
 *
 * `orderedIds` is the transcript's message ids in order. Only `auto`
 * delegations attribute (a `suggest` proposal didn't actually hand off).
 * Returns `root` when no delegation applies (the root skill answered).
 */
export function resolveMessageAgent(
  messageId: string,
  orderedIds: string[],
  delegations: readonly AgentDelegation[],
  root: AgentAttribution,
): AgentAttribution {
  const p = orderedIds.indexOf(messageId);
  if (p < 0) return root;
  let bestPos = -1;
  let best: AgentAttribution = root;
  for (const d of delegations) {
    if (d.mode !== "auto" || !d.afterMessageId) continue;
    const anchor = orderedIds.indexOf(d.afterMessageId);
    if (anchor < 0 || anchor >= p) continue; // must have happened before this message
    if (anchor >= bestPos) {
      bestPos = anchor;
      best = { avatar: d.avatar, label: d.targetDisplay };
    }
  }
  return best;
}

/** Attribution for every id in `orderedIds`, keyed by id. */
export function buildAgentMap(
  orderedIds: string[],
  delegations: readonly AgentDelegation[],
  root: AgentAttribution,
): Map<string, AgentAttribution> {
  const map = new Map<string, AgentAttribution>();
  for (const id of orderedIds) {
    map.set(id, resolveMessageAgent(id, orderedIds, delegations, root));
  }
  return map;
}
