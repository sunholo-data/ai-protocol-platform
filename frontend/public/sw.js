/* Platform service worker — makes the app installable to a home screen and
 * gives it an honest offline page, WITHOUT ever serving a stale app.
 *
 * Staleness is the failure that matters for a home-screen app, so the rules
 * are:
 *
 *   - Navigations (HTML) are NETWORK-ONLY. Online, every launch and reload gets
 *     the current build's HTML. Offline, the user gets /offline.html — never a
 *     cached page from an older build (old markup + new chunks is the classic
 *     PWA white screen).
 *   - Only Next's content-hashed `/_next/static/` files are cached. A hash names
 *     exactly one version of a file, so a cache hit can't be stale.
 *   - Caches are keyed on the build id this worker was registered with
 *     (`/sw.js?v=<build>`, see components/AppUpdater.tsx). Every deploy
 *     registers a new worker, which deletes every other cache on activate —
 *     including anything left by an older worker on this origin (the v5 app's
 *     worker, which the kill-switch this file replaced existed to clean up).
 *   - Nothing under /api/ is touched: per-user, authenticated, often streamed.
 *     A cached /api response is a data leak on a shared device, and a cached
 *     401 is a "logged out" that survives reloads.
 *
 * Opt out per deployment with NEXT_PUBLIC_SERVICE_WORKER=0: the page then
 * unregisters this worker and clears its caches instead.
 */

const VERSION = new URL(self.location.href).searchParams.get("v") || "0";
const CACHE_PREFIX = "platform-";
const CACHE = `${CACHE_PREFIX}${VERSION}`;
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE_URL)));
  // Safe to take over at once: this worker never serves HTML from cache, so
  // swapping workers mid-session cannot pair old markup with new chunks.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  // Other origins (the MCP-App sandbox, Firebase, fonts) keep their own story.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then((hit) => hit || Response.error()),
      ),
    );
    return;
  }

  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(request, copy));
            }
            return res;
          }),
      ),
    );
  }
  // Everything else: straight to the network (and the browser's HTTP cache).
});

/* ── Web Push (docs/design/v6.40.0/web-push-notifications.md) ────────────────
 *
 * The payload is `{title, body, url, tag}` — the skill's name and a link, never
 * message content (the text lands on a lock screen; the app loads the answer
 * over the authenticated connection when tapped).
 *
 * A push MUST always show a notification: iOS revokes push permission from a
 * web app that receives pushes without displaying one. So an unreadable payload
 * still shows a generic notice rather than nothing.
 */
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const title = typeof data.title === "string" && data.title ? data.title : "New activity";
  const options = {
    body: typeof data.body === "string" ? data.body : "Open the app to see it.",
    tag: typeof data.tag === "string" && data.tag ? data.tag : undefined,
    icon: "/images/logo/icon-192.png",
    badge: "/images/logo/icon-192.png",
    // Same-origin paths only — "/x", never "//other.host/x" (protocol-relative).
    data: { url: typeof data.url === "string" && /^\/(?!\/)/.test(data.url) ? data.url : "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Tapping a notification opens its conversation: reuse an open window (focus
// it, then navigate) so the home-screen app doesn't stack copies of itself.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        await client.focus();
        if ("navigate" in client) return client.navigate(url);
        return undefined;
      }
      return self.clients.openWindow(url);
    }),
  );
});
