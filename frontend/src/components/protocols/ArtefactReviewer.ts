// Pluggable artefact-render content review hook.
//
// Sprint 2.13 (v6.2.0). The Protocol is consulted before any MCP-app
// artefact is rendered. Forks plug their own reviewer (e.g. AIPLA's
// static-analysis ruleset); the platform ships a permissive default
// — existing demos render unchanged.
//
// The hook is ABOVE the iframe sandbox + CSP layer — defence-in-depth,
// not replacement. A reviewer that crashes or is bypassed leaves the
// sandbox + CSP boundary intact.
//
// Design contract: docs/design/v6.2.0/artefact-render-hook.md.
//
// The TypeScript interface MUST mirror the Python Protocol at
// backend/protocols/artefact_review.py exactly. Forks may implement
// the same policy in either layer and switch deployment-side without
// rewriting. JSON wire shape converts on the boundary.

/**
 * Input the reviewer sees before an artefact is rendered. camelCase
 * to match TS conventions; the Python mirror uses snake_case.
 */
export interface ArtefactReview {
  /** The MCP tool that produced this artefact (e.g. "physics_sim_builder"). */
  toolName: string;
  /** The MCP server id (`tool_configs.mcp.servers` key). */
  serverId: string;
  /** The artefact's resource URI (e.g. "ui://render/abc"). */
  resourceUri: string;
  /** The rendered HTML body the reviewer inspects. */
  html: string;
  /** The resource's `_meta.ui.csp`, or null if not provided. */
  csp: string | null;
  /** The tool result's structured payload (parsed JSON). */
  structuredContent: unknown;
  /** ADK invocation id — used for audit log + idempotency. */
  invocationId: string;
}

/**
 * The reviewer's answer. Discriminated union on `action` — TypeScript
 * narrows the variant in if/switch branches.
 */
export type ArtefactDecision =
  | { action: "approve" }
  | { action: "warn"; message: string; reasonCode: string }
  | {
      action: "block";
      message: string;
      reasonCode: string;
      appealUrl?: string;
    };

/**
 * The Protocol forks implement. Duck-typed — no inheritance required.
 * The platform calls `review` before any artefact iframe is mounted.
 */
export interface ArtefactReviewer {
  review(input: ArtefactReview): Promise<ArtefactDecision>;
}

/**
 * Shipped default. Approves everything — preserves pre-2.13 behaviour.
 * Forks override via `setArtefactReviewer(...)` at app bootstrap.
 */
export const PermissiveArtefactReviewer: ArtefactReviewer = {
  async review() {
    return { action: "approve" };
  },
};

// ─── Registry ───────────────────────────────────────────────────────────────

let _registered: ArtefactReviewer | null = null;

/**
 * Register the process-wide reviewer. Calling twice replaces the
 * previous registration (no warning — late registration is a valid
 * pattern for test fixtures).
 *
 * Throws if `impl` doesn't have a `review` method — fork misconfiguration
 * should fail loud at startup, not silently approve everything.
 */
export function setArtefactReviewer(impl: ArtefactReviewer): void {
  if (!impl || typeof impl.review !== "function") {
    throw new TypeError(
      "setArtefactReviewer requires an ArtefactReviewer with an async review() method",
    );
  }
  _registered = impl;
}

/**
 * Return the registered reviewer, or the permissive default when no
 * fork has plugged one. NEVER returns null — call sites stay clean
 * of nullability checks.
 */
export function getArtefactReviewer(): ArtefactReviewer {
  return _registered ?? PermissiveArtefactReviewer;
}

/**
 * Drop the registered reviewer (resets to permissive default). Used
 * by tests; not for production code.
 */
export function clearArtefactReviewer(): void {
  _registered = null;
}
