// M2A — host-side adapter for "notify"-style messages from the MCP App iframe.
//
// MCP Apps lets a guest UI iframe send `notifications/message` (or whatever
// the host-app contract dictates) back through AppBridge. AppRenderer
// surfaces those via its `onMessage` prop. We translate the small set of
// known shapes into a chat string the user would have typed, so the active
// integration loop is "click the iframe → new chat turn".
//
// Forward-compatibility: unknown shapes return null. The router uses null
// as a no-op signal, never blocks rendering. Defensive parsing because the
// iframe is sandboxed and untrusted — anything weird returns null.

const NOTIFY_TYPE = "app/notify" as const;

type Notify = {
  type: string;
  reason?: string;
  payload?: Record<string, unknown>;
};

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asNotify(v: unknown): Notify | null {
  if (!isObject(v)) return null;
  if (v.type !== NOTIFY_TYPE) return null;
  return {
    type: v.type as string,
    reason: typeof v.reason === "string" ? v.reason : undefined,
    payload: isObject(v.payload) ? v.payload : undefined,
  };
}

function locationSelected(payload: Record<string, unknown>): string | null {
  const location = payload.location;
  if (typeof location !== "string" || !location) return null;
  return `Tell me more about ${location}`;
}

function routeSelected(payload: Record<string, unknown>): string | null {
  const { from, to } = payload;
  if (
    typeof from !== "string" || typeof to !== "string" ||
    !from || !to
  ) {
    return null;
  }
  return `Tell me about the route from ${from} to ${to}`;
}

/**
 * Translate a guest-iframe notification into a chat-string the user would
 * have typed. Returns null for anything we don't recognise so the caller
 * can no-op safely.
 */
export function notificationToChatMessage(
  notification: unknown,
): string | null {
  const n = asNotify(notification);
  if (!n || !n.reason || !n.payload) return null;

  switch (n.reason) {
    case "location-selected":
      return locationSelected(n.payload);
    case "route-selected":
      return routeSelected(n.payload);
    default:
      return null;
  }
}
