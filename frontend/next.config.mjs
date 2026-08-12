/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL,
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
