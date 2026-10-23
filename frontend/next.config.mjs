// One id per `next build`, baked into BOTH the client bundle and the server
// (/api/version). A page whose baked id differs from the server's is running a
// superseded build — the signal the home-screen app uses to offer a reload, and
// the version the service worker keys its caches on. A timestamp, not the git
// sha: the frontend image does not receive COMMIT_SHA at build time, and a
// promote rebuilds the UI, so "same commit" is not "same bundle" anyway.
//
// Pinned into process.env on first evaluation: `next build` evaluates this file
// in the main process AND again in its compile workers, and each evaluation
// would otherwise mint its own timestamp — the client bundle and the server
// route then disagree and every tab sees a "new version" forever (caught on
// the live Sunholo service, 2026-09-30). Workers inherit the pinned value.
const BUILD_ID = (process.env.NEXT_PUBLIC_BUILD_ID ||= Date.now().toString(36))

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL,
    NEXT_PUBLIC_BUILD_ID: BUILD_ID,
  },
  // The service worker script must never come from an HTTP cache, or a
  // deploy's new worker is invisible until that cache entry expires.
  async headers() {
    return [
      { source: '/sw.js', headers: [{ key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' }] },
    ]
  },
  serverRuntimeConfig: {
    MAILGUN_WEBHOOK_SECRET: process.env.MAILGUN_WEBHOOK_SECRET,
  },
  // G45 / Sprint A2A-INVOKE — Friction 25: Next.js's catch-all 404s every
  // `/a2a/*` request before FastAPI ever sees it. Forward them to the
  // backend sidecar so peers can POST JSON-RPC to the A2A invocation
  // surface mounted at /a2a (see backend/protocols/a2a_invocation.py).
  async rewrites() {
    const backend = process.env.BACKEND_URL || 'http://127.0.0.1:1956'
    return [
      { source: '/a2a',        destination: `${backend}/a2a/` },
      { source: '/a2a/:path*', destination: `${backend}/a2a/:path*` },
    ]
  },
}

export default nextConfig
