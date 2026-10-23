// Authenticated images inside an A2UI surface.
//
// An A2UI `Image` renders as a plain `<img src=…>`, and an `<img>` carries NO
// custom request headers. So a component pointing at a backend route that
// requires a Firebase Bearer fails twice over:
//
//   1. the url the backend emits is `/api/browser/artefacts/<id>` — backend-
//      relative. In the browser that resolves against the FRONTEND origin,
//      which has no such route, so it 404s;
//   2. corrected to `/api/proxy/api/browser/artefacts/<id>` it reaches the
//      backend, but the proxy passes the client's Authorization header through
//      UNTOUCHED and never injects one — so an <img> arrives anonymous and
//      gets a 401.
//
// This is the same wall `DocumentViewer` hit with `<iframe>` and solved the
// same way: fetch through `fetchWithAuth` (JS can set headers), wrap the bytes
// in a `blob:` URL, and hand the element something that needs no auth at all.
//
// It lives at the A2UI layer rather than in a browser-specific component
// because it is not browser-specific: ANY surface that shows an image from an
// authenticated backend route has this problem. Per CLAUDE.md #7 the fix
// belongs in the generic render path, not in a component keyed off one tool.
//
// Trade-off, same as DocumentViewer's: the pane paints first with an empty
// image slot and the picture arrives a moment later. That is deliberate —
// blocking the whole surface on an image download would make a slow fetch look
// like a broken pane.

import { fetchWithAuth } from "@/lib/apiClient";
import { logger } from "@/lib/logger";

/** Backend paths reachable only through the authenticated proxy. */
const BACKEND_PATH = /^\/api\//;
const PROXY_PREFIX = "/api/proxy";

interface ImageComponent {
  id?: string;
  component?: string;
  url?: string;
  [k: string]: unknown;
}

/** Every `Image` component in these messages whose url needs authenticating. */
export function findAuthImages(messages: readonly Record<string, unknown>[]): ImageComponent[] {
  const found: ImageComponent[] = [];
  for (const message of messages) {
    const update = message?.updateComponents as { components?: unknown[] } | undefined;
    if (!update?.components) continue;
    for (const raw of update.components) {
      const c = raw as ImageComponent;
      if (
        c?.component === "Image" &&
        typeof c.url === "string" &&
        BACKEND_PATH.test(c.url) &&
        !c.url.startsWith(PROXY_PREFIX)
      ) {
        found.push(c);
      }
    }
  }
  return found;
}

/**
 * Fetch each authenticated image and return `updateComponents` messages that
 * swap its url for a `blob:` one.
 *
 * Never throws: an image that cannot be fetched leaves the surface exactly as
 * it was rather than taking the pane down with it. The failure IS logged —
 * a picture that silently never arrives is the bug this whole file is fixing,
 * so it must not fail silently in turn (CLAUDE.md #8).
 */
export async function resolveAuthImages(
  surfaceId: string,
  messages: readonly Record<string, unknown>[],
): Promise<{ patches: Record<string, unknown>[]; revoke: string[] }> {
  const targets = findAuthImages(messages);
  const patches: Record<string, unknown>[] = [];
  const revoke: string[] = [];

  await Promise.all(
    targets.map(async (component) => {
      const path = `${PROXY_PREFIX}${component.url}`;
      try {
        const res = await fetchWithAuth(path);
        if (!res.ok) {
          logger.warn("a2ui_auth_image_failed", { surfaceId, path, status: res.status });
          return;
        }
        const objectUrl = URL.createObjectURL(await res.blob());
        revoke.push(objectUrl);
        patches.push({
          version: "v0.9",
          updateComponents: {
            surfaceId,
            components: [{ ...component, url: objectUrl }],
          },
        });
      } catch (err) {
        logger.warn("a2ui_auth_image_error", {
          surfaceId,
          path,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }),
  );

  return { patches, revoke };
}
