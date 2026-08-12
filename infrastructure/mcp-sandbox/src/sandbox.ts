// Aitana MCP App sandbox proxy bridge.
//
// This file runs INSIDE the outer sandbox iframe (served from a different
// origin than the host). It creates an inner iframe for untrusted MCP App
// HTML content and relays JSON-RPC postMessage between the host (parent
// window) and the inner content.
//
// Per MCP Apps spec — the host and the sandbox MUST have different origins.
// Without that, `allow-same-origin` on the inner iframe lets the inner
// content read host cookies / localStorage. The whole double-iframe dance
// exists to satisfy that boundary.
//
// Adapted from `modelcontextprotocol/ext-apps/examples/basic-host/src/sandbox.ts`
// (commit 0008d3b7, ext-apps 1.7.1). Differences from the reference:
// - Allowed referrer regex pulled from a build-time constant injected by
//   serve.ts via window.__AITANA_SANDBOX_CONFIG__ — so the regex isn't
//   hard-coded to localhost and can be set per-env from ALLOWED_HOST_ORIGINS
// - Logging prefix changed from [Sandbox] to [aitana-sandbox]

// Inlined from @modelcontextprotocol/ext-apps/app-bridge so the bundle
// stays tiny (~3KB) — importing the full app-bridge surface pulled in
// 350KB+ of MCP SDK + Protocol class even with tree-shaking. This bridge
// only needs (a) the resource-ready notification method name, (b) a tiny
// permissions-to-allow-attribute mapper. Both are stable parts of the spec
// (verified against ext-apps@1.7.1 commit 0008d3b7).

const RESOURCE_READY_NOTIFICATION = "ui/notifications/sandbox-resource-ready" as const;
const PROXY_READY_NOTIFICATION = "ui/notifications/sandbox-proxy-ready" as const;

interface McpUiResourcePermissions {
  camera?: unknown;
  microphone?: unknown;
  geolocation?: unknown;
  clipboardWrite?: unknown;
}

function buildAllowAttribute(permissions: McpUiResourcePermissions | undefined): string {
  if (!permissions) return "";
  const allowList: string[] = [];
  if (permissions.camera) allowList.push("camera");
  if (permissions.microphone) allowList.push("microphone");
  if (permissions.geolocation) allowList.push("geolocation");
  if (permissions.clipboardWrite) allowList.push("clipboard-write");
  return allowList.join("; ");
}

interface AitanaSandboxConfig {
  allowedReferrerPattern: string;
}
const sandboxConfig: AitanaSandboxConfig | undefined = (
  window as unknown as { __AITANA_SANDBOX_CONFIG__?: AitanaSandboxConfig }
).__AITANA_SANDBOX_CONFIG__;

const ALLOWED_REFERRER_PATTERN = new RegExp(
  sandboxConfig?.allowedReferrerPattern ?? "^http://localhost:3456(:|/|$)",
);

console.log("[aitana-sandbox] boot", {
  href: window.location.href,
  referrer: document.referrer,
  pattern: ALLOWED_REFERRER_PATTERN.source,
  isTop: window.self === window.top,
});

if (window.self === window.top) {
  throw new Error(
    "[aitana-sandbox] This file is only to be used in an iframe sandbox.",
  );
}

if (!document.referrer) {
  throw new Error(
    "[aitana-sandbox] No referrer; cannot validate embedding site.",
  );
}

if (!document.referrer.match(ALLOWED_REFERRER_PATTERN)) {
  throw new Error(
    `[aitana-sandbox] Embedding domain not allowed in referrer ${document.referrer}. ` +
      `Update ALLOWED_HOST_ORIGINS on the sandbox service to allow your host.`,
  );
}

// The expected host origin for all parent messages we'll receive.
const EXPECTED_HOST_ORIGIN = new URL(document.referrer).origin;

// Our own origin — every message we receive from the inner iframe MUST come
// from this origin (because we wrote the inner content via document.write
// and the inner inherits our origin via allow-same-origin).
const OWN_ORIGIN = new URL(window.location.href).origin;

// Security self-test: if window.top is reachable, the sandbox config is
// catastrophically broken and we should abort immediately.
try {
  // Intentionally probing cross-origin top access; expect SecurityError.
  (window.top as Window).alert(
    "[aitana-sandbox] If you see this, the sandbox is not setup securely.",
  );
  throw "FAIL";
} catch (e) {
  if (e === "FAIL") {
    throw new Error("[aitana-sandbox] The sandbox is not setup securely.");
  }
  // Expected: SecurityError → properly sandboxed.
}

// Create the inner iframe that will host the actual MCP App HTML.
// allow-scripts + allow-same-origin + allow-forms is the spec default.
// Cookies/localStorage are scoped to the SANDBOX origin (not the host's),
// because we're on a different origin from the parent — that's the whole
// point of the double-iframe architecture.
const inner = document.createElement("iframe");
inner.style.cssText = "width:100%; height:100%; border:none;";
inner.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
document.body.appendChild(inner);

// Bidirectional message relay:
//
//   Host (parent window) ↔ Sandbox (us, outer frame) ↔ View (inner iframe)
//
// Parent and inner have different origins — they can't talk directly. We
// forward in both directions to bridge them.
//
// One special case: the "sandbox-resource-ready" message is INTERCEPTED
// here (not relayed). It carries the HTML to load into the inner iframe
// and the sandbox/permissions to apply. We use document.write rather than
// srcdoc because srcdoc breaks some libraries (notably CesiumJS).
window.addEventListener("message", (event) => {
  if (event.source === window.parent) {
    // Validate parent origin to prevent malicious pages from spoofing the
    // host and pushing data into our sandbox.
    if (event.origin !== EXPECTED_HOST_ORIGIN) {
      console.error(
        "[aitana-sandbox] Rejecting message from unexpected origin:",
        event.origin,
        "expected:",
        EXPECTED_HOST_ORIGIN,
      );
      return;
    }

    if (event.data && event.data.method === RESOURCE_READY_NOTIFICATION) {
      const { html, sandbox, permissions } = event.data.params;
      if (typeof sandbox === "string") {
        inner.setAttribute("sandbox", sandbox);
      }
      const allowAttribute = buildAllowAttribute(permissions);
      if (allowAttribute) {
        console.log("[aitana-sandbox] Setting allow attribute:", allowAttribute);
        inner.setAttribute("allow", allowAttribute);
      }
      if (typeof html === "string") {
        const doc = inner.contentDocument || inner.contentWindow?.document;
        if (doc) {
          doc.open();
          doc.write(html);
          doc.close();
          // DETERMINISTIC SIZING — the host is cross-origin and CANNOT read the
          // artefact's content height. We (same-origin with it via
          // allow-same-origin) measure the content and report it, so the host
          // sizes the outer iframe to the app instead of clipping it to a strip.
          const reportSize = () => {
            const h = Math.max(
              doc.documentElement?.scrollHeight ?? 0,
              doc.documentElement?.offsetHeight ?? 0,
              doc.body?.scrollHeight ?? 0,
            );
            if (h > 0) {
              window.parent.postMessage(
                { jsonrpc: "2.0", method: "ui/notifications/size", params: { height: h } },
                EXPECTED_HOST_ORIGIN,
              );
            }
          };
          try {
            const ro = new ResizeObserver(reportSize);
            ro.observe(doc.documentElement);
            if (doc.body) ro.observe(doc.body);
          } catch {
            // ResizeObserver unavailable — fall back to the timed reports below.
          }
          reportSize();
          inner.contentWindow?.addEventListener("load", reportSize);
          setTimeout(reportSize, 150);
          setTimeout(reportSize, 600);
        } else {
          console.warn(
            "[aitana-sandbox] document.write not available; falling back to srcdoc",
          );
          inner.srcdoc = html;
        }
      }
    } else if (inner.contentWindow) {
      inner.contentWindow.postMessage(event.data, "*");
    }
  } else if (event.source === inner.contentWindow) {
    // Validate inner-frame origin matches our own.
    if (event.origin !== OWN_ORIGIN) {
      console.error(
        "[aitana-sandbox] Rejecting message from inner iframe with unexpected origin:",
        event.origin,
        "expected:",
        OWN_ORIGIN,
      );
      return;
    }
    // Forward to parent with explicit origin (NOT '*') so a third-party
    // window that opened us can't intercept.
    window.parent.postMessage(event.data, EXPECTED_HOST_ORIGIN);
  }
});

// Tell the host we're ready to receive view HTML.
console.log("[aitana-sandbox] sending proxy-ready to", EXPECTED_HOST_ORIGIN);
window.parent.postMessage(
  {
    jsonrpc: "2.0",
    method: PROXY_READY_NOTIFICATION,
    params: {},
  },
  EXPECTED_HOST_ORIGIN,
);
