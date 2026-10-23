import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// LOCAL_MODE detection is read from `process.env.NEXT_PUBLIC_LOCAL_MODE`
// via the `isLocalMode()` helper. Mock the helper directly — Next.js
// inlines NEXT_PUBLIC_* at build time so runtime monkeypatching env
// doesn't propagate. Easier seam = mock the function.
vi.mock("@/lib/localMode", async () => {
  const actual = await vi.importActual<typeof import("@/lib/localMode")>(
    "@/lib/localMode",
  );
  const isLocalMode = vi.fn(() => false);
  return {
    ...actual,
    isLocalMode,
    // The real helper calls the module-internal isLocalMode, which the mock
    // can't reach — delegate so the tests below keep driving one switch.
    isLocalModeBannerEnabled: vi.fn(() => isLocalMode()),
  };
});

import * as localMode from "@/lib/localMode";
import { LocalModeBanner } from "@/components/LocalModeBanner";

describe("LocalModeBanner", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(localMode.isLocalMode).mockReturnValue(false);
  });

  it("renders nothing when LOCAL_MODE is off", () => {
    const { container } = render(<LocalModeBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the LOCAL_MODE banner when LOCAL_MODE is on", () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              local_mode: true,
              disabled_services: ["firestore", "firebase_auth"],
            }),
        }),
      ) as unknown as typeof fetch,
    );

    render(<LocalModeBanner />);

    expect(
      screen.getByText(/LOCAL_MODE — All data is in-memory and ephemeral/i),
    ).toBeInTheDocument();
  });

  it("links to the workshop graduation guide", () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ local_mode: true, disabled_services: [] }),
        }),
      ) as unknown as typeof fetch,
    );

    render(<LocalModeBanner />);

    const link = screen.getByRole("link", { name: /Connect to your own GCP/i });
    expect(link).toHaveAttribute("href", "/workshop#graduating-from-local-mode");
  });

  it("shows the disabled-services list once fetched", async () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              local_mode: true,
              disabled_services: ["firestore", "vertex_search"],
            }),
        }),
      ) as unknown as typeof fetch,
    );

    render(<LocalModeBanner />);

    await waitFor(() => {
      expect(
        screen.getByText(/Disabled: firestore, vertex_search/),
      ).toBeInTheDocument();
    });
  });

  it("still renders the banner even if the status fetch fails", async () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))) as unknown as typeof fetch,
    );

    render(<LocalModeBanner />);

    // Wait a microtask to let the fetch promise reject.
    await waitFor(() => {
      expect(
        screen.getByText(/LOCAL_MODE — All data is in-memory and ephemeral/i),
      ).toBeInTheDocument();
    });
  });

  it("stays hidden in LOCAL_MODE when the deployment opts out", () => {
    vi.mocked(localMode.isLocalMode).mockReturnValue(true);
    vi.mocked(localMode.isLocalModeBannerEnabled).mockReturnValue(false);

    const { container } = render(<LocalModeBanner />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("isLocalModeBannerEnabled", () => {
  it("follows LOCAL_MODE unless NEXT_PUBLIC_LOCAL_MODE_BANNER turns it off", async () => {
    const actual = await vi.importActual<typeof import("@/lib/localMode")>("@/lib/localMode");
    vi.stubEnv("NEXT_PUBLIC_LOCAL_MODE", "");
    expect(actual.isLocalModeBannerEnabled()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_LOCAL_MODE", "1");
    expect(actual.isLocalModeBannerEnabled()).toBe(true);
    vi.stubEnv("NEXT_PUBLIC_LOCAL_MODE_BANNER", "0");
    expect(actual.isLocalModeBannerEnabled()).toBe(false);
    vi.unstubAllEnvs();
  });
});
