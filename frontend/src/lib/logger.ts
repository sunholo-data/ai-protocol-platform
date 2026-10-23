// lib/logger.ts — FRONTEND-OBS M3
//
// Ships browser diagnostics to `POST /api/client-logs`, which re-emits them through
// the backend's existing StructuredFormatter into Cloud Logging → Error
// Reporting → BigQuery. Before this, the client tier had no telemetry at all:
// 45 console.* calls whose event names were already structured
// (`stream_run_failed`, `stream_truncated_no_terminal`) died in a console nobody
// was watching.
//
// WHY NOT A THIRD-PARTY SDK. Product Axiom #9 puts the trust boundary at the GCP
// project edge, and browser error capture is the worst case for an SDK that
// crosses it: breadcrumbs auto-capture console arguments, React integrations
// attach component props at throw time, and in this app those props are customer
// contract clauses. Browser → our own backend is not egress, so this path keeps
// the data inside the boundary by construction rather than by a scrubbing hook
// somebody must remember to update. See docs/design/v6.41.0/frontend-observability.md.
//
// WHY `fetch(keepalive)` AND NOT `sendBeacon`. The design doc said sendBeacon.
// It cannot be used: sendBeacon issues a POST with no custom headers, so it
// cannot carry `Authorization: Bearer <jwt>`. Using it would mean either putting
// the token in the request body or leaving the ingest route unauthenticated —
// the second is precisely the open log-injection endpoint M2's negative tests
// exist to prevent. `fetch(..., {keepalive: true})` also survives page unload,
// keeps the header, and caps at 64KB, which is the body cap we wanted anyway.
//
// THE OVERRIDING CONSTRAINT: this must never degrade the app it observes. It
// does not throw, does not retry in a loop, and does not grow without bound.
// A failure to log is always preferable to a failure to render.
//
// Field names mirror the OTel LogRecord (`timestamp`, `severityText`, `body`,
// `attributes`) so Phase 3's move to OTLP is a transport swap, not a re-model.

import { fetchWithAuth } from "@/lib/apiClient";

export const BATCH_SIZE = 20;
export const FLUSH_INTERVAL_MS = 5_000;
export const MAX_BUFFERED_EVENTS = 200;
/**
 * Bytes per request. Browsers cap keepalive bodies at 64KB IN FLIGHT and reject
 * the fetch outright past it, so the unload flush must never approach that;
 * 48KB leaves room for the envelope and a trailing `logger_dropped`. It also
 * bounds the ordinary flush: an error carries up to an 8KB stack, so a batch of
 * 20 by count alone could be ~250KB. Count and bytes are both limits.
 */
export const FLUSH_BYTE_BUDGET = 48 * 1024;
/** Matches the backend's per-event stack cap; truncate here so a batch is never rejected whole. */
export const MAX_STACK_CHARS = 8_192;
const INGEST_PATH = "/api/proxy/api/client-logs";
const MAX_CONSECUTIVE_FAILURES = 3;

export type Severity = "DEBUG" | "INFO" | "WARN" | "ERROR";

export interface ClientLogEvent {
  timestamp: string;
  severityText: Severity;
  body: string;
  attributes: Record<string, unknown>;
}

export interface LoggerContext {
  threadId?: string;
  sessionId?: string;
  skillId?: string;
}

export interface CreateLoggerOptions {
  enabled: boolean;
  /** Mirror to console as well as shipping. Defaults to on outside production. */
  mirrorToConsole?: boolean;
}

export interface ClientLogger {
  event(name: string, attributes?: Record<string, unknown>): void;
  warn(name: string, attributes?: Record<string, unknown>): void;
  error(error: unknown, attributes?: Record<string, unknown>): void;
  setContext(context: LoggerContext): void;
  flush(): Promise<void>;
  /** Called by the visibilitychange listener; exposed so it is testable without jsdom events. */
  handleVisibilityChange(state: DocumentVisibilityState): void;
  bufferedCount(): number;
  droppedCount(): number;
  /** Test-only view of the buffer. */
  peek(): ClientLogEvent[];
}

export function createLogger({
  enabled,
  mirrorToConsole = process.env.NODE_ENV !== "production",
}: CreateLoggerOptions): ClientLogger {
  let buffer: ClientLogEvent[] = [];
  // Approximate serialized size of `buffer`, maintained incrementally so the
  // byte budget costs one stringify per event, not one per flush.
  let bufferedBytes = 0;
  let dropped = 0;
  let context: LoggerContext = {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  let consecutiveFailures = 0;
  // Set while a flush is in flight so a timer tick cannot start a second one.
  let flushing = false;

  const sizeOf = (e: ClientLogEvent) => JSON.stringify(e).length;
  const overBudget = () => buffer.length >= BATCH_SIZE || bufferedBytes >= FLUSH_BYTE_BUDGET;

  function scheduleFlush() {
    if (timer !== null) return;
    // Back-off: after repeated failures, stretch the interval rather than
    // hammering a route that is already failing. A telemetry client retrying
    // every 5s through an outage is a self-inflicted load test.
    const backoff = Math.min(2 ** consecutiveFailures, 8);
    timer = setTimeout(() => {
      timer = null;
      void flush();
    }, FLUSH_INTERVAL_MS * backoff);
  }

  /** Remove and return the next batch: at most BATCH_SIZE events, within the byte budget, never empty. */
  function takeBatch(): ClientLogEvent[] {
    const batch: ClientLogEvent[] = [];
    let bytes = 0;
    while (buffer.length > 0 && batch.length < BATCH_SIZE) {
      const size = sizeOf(buffer[0]);
      // Always take at least one, so a single oversized event cannot wedge the
      // queue forever (the stack cap makes that impossible anyway).
      if (batch.length > 0 && bytes + size > FLUSH_BYTE_BUDGET) break;
      batch.push(buffer.shift() as ClientLogEvent);
      bytes += size;
      bufferedBytes -= size;
    }
    if (buffer.length === 0) bufferedBytes = 0;
    return batch;
  }

  /** POST one batch. Never throws; failures only adjust the back-off. */
  async function send(batch: ClientLogEvent[], keepalive: boolean): Promise<void> {
    const droppedSoFar = dropped;
    dropped = 0;
    const events = droppedSoFar
      ? [
          ...batch,
          {
            timestamp: new Date().toISOString(),
            severityText: "WARN" as const,
            body: "logger_dropped",
            attributes: { ...context, droppedCount: droppedSoFar },
          },
        ]
      : batch;
    try {
      const res = await fetchWithAuth(INGEST_PATH, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events }),
        // Survives page unload; unlike sendBeacon it keeps the Bearer header.
        keepalive,
      });
      consecutiveFailures = res.ok ? 0 : Math.min(consecutiveFailures + 1, MAX_CONSECUTIVE_FAILURES);
    } catch {
      // Swallowed on purpose. There is nowhere to report a reporting failure,
      // and re-throwing here would surface inside whatever render or handler
      // happened to trigger the flush. The batch is NOT requeued: a retry queue
      // that survives an outage is how a logger turns a backend blip into
      // unbounded client memory growth.
      consecutiveFailures = Math.min(consecutiveFailures + 1, MAX_CONSECUTIVE_FAILURES);
    }
  }

  function push(severityText: Severity, body: string, attributes: Record<string, unknown> = {}) {
    if (mirrorToConsole) {
      const line = `[${severityText}] ${body}`;
      if (severityText === "ERROR") console.error(line, attributes);
      else if (severityText === "WARN") console.warn(line, attributes);
      else console.info(line, attributes);
    }
    if (!enabled) return;

    const event: ClientLogEvent = {
      timestamp: new Date().toISOString(),
      severityText,
      body,
      attributes: { ...context, ...attributes },
    };
    buffer.push(event);
    bufferedBytes += sizeOf(event);

    // Drop the OLDEST on overflow. The events nearest a failure are the ones
    // worth keeping; the ones from five minutes ago are not what anybody is
    // about to debug.
    if (buffer.length > MAX_BUFFERED_EVENTS) {
      const overflow = buffer.length - MAX_BUFFERED_EVENTS;
      for (const e of buffer.splice(0, overflow)) bufferedBytes -= sizeOf(e);
      dropped += overflow;
    }

    if (overBudget()) void flush();
    else scheduleFlush();
  }

  async function flush(): Promise<void> {
    if (!enabled || flushing || buffer.length === 0) return;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    flushing = true;
    try {
      await send(takeBatch(), false);
    } finally {
      flushing = false;
      // A budget-limited batch can leave a still-full buffer behind; drain it
      // now rather than in 5s. Each send removes its batch, so this terminates.
      if (overBudget()) void flush();
      else if (buffer.length > 0) scheduleFlush();
    }
  }

  /** The page is going away: send everything, in budget-sized batches, with keepalive. */
  function drainOnHide() {
    if (!enabled) return;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    // Deliberately NOT gated on `flushing`: an in-flight ordinary fetch may be
    // cancelled by the unload; what is still buffered must go now regardless.
    while (buffer.length > 0) void send(takeBatch(), true);
  }

  return {
    event: (name, attributes) => push("INFO", name, attributes),
    warn: (name, attributes) => push("WARN", name, attributes),
    error: (error, attributes) => {
      const err = error instanceof Error ? error : new Error(String(error));
      push("ERROR", err.name || "Error", {
        ...attributes,
        errorName: err.name,
        errorMessage: err.message,
        stack: (err.stack ?? "").slice(0, MAX_STACK_CHARS),
      });
    },
    setContext: (next) => {
      context = { ...context, ...next };
    },
    flush: () => flush(),
    handleVisibilityChange: (state) => {
      if (state === "hidden") drainOnHide();
    },
    bufferedCount: () => buffer.length,
    droppedCount: () => dropped,
    peek: () => [...buffer],
  };
}

/**
 * The app-wide logger. `NEXT_PUBLIC_CLIENT_LOGGING=off` is the kill switch —
 * it degrades to console-only, which is exactly the pre-M3 behaviour.
 *
 * INERT UNDER TEST, unconditionally. jsdom has a `window`, so without this
 * guard the singleton enables itself in vitest and its first flush walks the
 * real `fetchWithAuth → getIdToken → Firebase` path that every other test in
 * this repo mocks. Locally that hides behind per-file forks vitest kills; in
 * CI (`singleFork: true`) the shared process never exits and the build hangs
 * at ci-gate-frontend — which is exactly what happened on 2026-09-04. Tests
 * that want shipping behaviour use `createLogger({ enabled: true })` with the
 * transport mocked, as logger.test.ts does.
 */
const _underTest = process.env.NODE_ENV === "test" || Boolean(process.env.VITEST);
export const logger: ClientLogger = createLogger({
  enabled:
    !_underTest && typeof window !== "undefined" && process.env.NEXT_PUBLIC_CLIENT_LOGGING !== "off",
  // The console mirror stays on under test: it is side-effect free, and
  // existing tests assert console.error fires on real faults (a duplicate
  // surface mount, for one). Only the transport is switched off.
});

let handlersInstalled = false;

/**
 * Registers the half of client error capture that React error boundaries
 * cannot see: errors thrown in event handlers and unhandled promise
 * rejections. FRONTEND-OBS M1's boundaries cover render throws; neither half
 * is sufficient alone. Idempotent.
 */
export function installGlobalHandlers(target: Window = window): () => void {
  if (handlersInstalled) return () => {};
  handlersInstalled = true;

  const onError = (e: ErrorEvent) => {
    logger.error(e.error ?? new Error(e.message), { source: "window.onerror" });
  };
  const onRejection = (e: PromiseRejectionEvent) => {
    logger.error(e.reason, { source: "unhandledrejection" });
  };
  const onVisibility = () => {
    logger.handleVisibilityChange(target.document.visibilityState);
  };

  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  target.document.addEventListener("visibilitychange", onVisibility);

  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
    target.document.removeEventListener("visibilitychange", onVisibility);
    handlersInstalled = false;
  };
}
