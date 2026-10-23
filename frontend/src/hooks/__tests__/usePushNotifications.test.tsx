import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { urlBase64ToUint8Array, usePushNotifications } from "@/hooks/usePushNotifications";

const KEY = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

function ok(body?: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

type FakeSub = { endpoint: string; toJSON: () => object; unsubscribe: () => Promise<boolean> };
let subscription: FakeSub | null;
const pushManager = {
  getSubscription: vi.fn(async () => subscription),
  subscribe: vi.fn(async () => {
    subscription = {
      endpoint: "https://push.example/abc",
      toJSON: () => ({ endpoint: "https://push.example/abc", keys: { p256dh: "p", auth: "a" } }),
      unsubscribe: vi.fn(async () => true),
    };
    return subscription;
  }),
};

function installBrowser({ permission = "default" as NotificationPermission, ua = "Mozilla/5.0 (Macintosh)" } = {}) {
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve({ pushManager }) },
  });
  Object.defineProperty(window, "PushManager", { configurable: true, value: function PushManager() {} });
  const N = Object.assign(function Notification() {}, {
    permission,
    requestPermission: vi.fn(async () => {
      (N as { permission: NotificationPermission }).permission = "granted";
      return "granted" as NotificationPermission;
    }),
  });
  Object.defineProperty(window, "Notification", { configurable: true, writable: true, value: N });
  Object.defineProperty(navigator, "userAgent", { configurable: true, value: ua });
  Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
  return N;
}

describe("usePushNotifications", () => {
  beforeEach(() => {
    subscription = null;
    vi.mocked(fetchWithAuth).mockReset();
  });
  afterEach(() => {
    // jsdom has none of these; remove so other tests see a plain browser.
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    delete (window as { PushManager?: unknown }).PushManager;
  });

  it("is unsupported without service worker / push support", async () => {
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("unsupported"));
  });

  it("asks for the Home Screen in an iPhone Safari tab", async () => {
    installBrowser({ ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X)" });
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("ios-needs-home-screen"));
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("hides when the server has push turned off", async () => {
    installBrowser();
    vi.mocked(fetchWithAuth).mockResolvedValueOnce(ok({ enabled: false, publicKey: null }));
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("disabled-by-server"));
  });

  it("reports blocked when permission was denied", async () => {
    installBrowser({ permission: "denied" });
    vi.mocked(fetchWithAuth).mockResolvedValueOnce(ok({ enabled: true, publicKey: KEY }));
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("blocked"));
  });

  it("enables: asks permission, subscribes with the VAPID key, registers the device", async () => {
    const N = installBrowser();
    vi.mocked(fetchWithAuth)
      .mockResolvedValueOnce(ok({ enabled: true, publicKey: KEY }))
      .mockResolvedValueOnce({ ok: true, status: 204 } as Response);
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("off"));

    await act(async () => {
      await result.current.enable();
    });

    expect(N.requestPermission).toHaveBeenCalled();
    expect(pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(KEY) }),
    );
    const [url, init] = vi.mocked(fetchWithAuth).mock.calls[1];
    expect(url).toBe("/api/proxy/api/push/subscribe");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body)).subscription.endpoint).toBe("https://push.example/abc");
    expect(result.current.state).toBe("on");
  });

  it("surfaces a server refusal instead of claiming it's on", async () => {
    installBrowser();
    vi.mocked(fetchWithAuth)
      .mockResolvedValueOnce(ok({ enabled: true, publicKey: KEY }))
      .mockResolvedValueOnce({ ok: false, status: 500 } as Response);
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("off"));
    await act(async () => {
      await result.current.enable();
    });
    expect(result.current.state).toBe("off");
    expect(result.current.error).toMatch(/500/);
  });

  it("disables: unregisters the endpoint and unsubscribes", async () => {
    installBrowser({ permission: "granted" });
    const unsubscribe = vi.fn(async () => true);
    subscription = { endpoint: "https://push.example/abc", toJSON: () => ({}), unsubscribe };
    vi.mocked(fetchWithAuth)
      .mockResolvedValueOnce(ok({ enabled: true, publicKey: KEY }))
      .mockResolvedValueOnce({ ok: true, status: 204 } as Response);
    const { result } = renderHook(() => usePushNotifications());
    await waitFor(() => expect(result.current.state).toBe("on"));
    await act(async () => {
      await result.current.disable();
    });
    const [url, init] = vi.mocked(fetchWithAuth).mock.calls[1];
    expect(url).toBe("/api/proxy/api/push/subscribe");
    expect(init?.method).toBe("DELETE");
    expect(JSON.parse(String(init?.body))).toEqual({ endpoint: "https://push.example/abc" });
    expect(unsubscribe).toHaveBeenCalled();
    expect(result.current.state).toBe("off");
  });
});
