/**
 * /group page tests (sprint 2.11, M3).
 *
 * Test matcher convention: use substring matchers (/text/i, not /^text$/i) for
 * user-visible strings so tests survive button-text rebranding in downstream forks.
 * Reserve anchored matchers for internal IDs or programmatic names that must not drift.
 *
 * Covers:
 *   - render in anonymous-group mode (form visible)
 *   - render in other modes (friendly "not available" message)
 *   - happy join → redirect to /
 *   - error states render typed messages
 *   - button disabled while joining
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the auth-mode helper so each test can switch modes.
const isAnonymousGroupAuthModeMock = vi.fn();
vi.mock("@/lib/anonymousGroupAuth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/anonymousGroupAuth")>();
  return {
    ...actual,
    isAnonymousGroupAuthMode: () => isAnonymousGroupAuthModeMock(),
  };
});

// Mock Next.js router; capture replace() calls.
const routerReplaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: routerReplaceMock,
    push: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

import { AnonymousGroupAuthProvider } from "@/contexts/AnonymousGroupAuthProvider";

function wrap(node: ReactNode) {
  return <AnonymousGroupAuthProvider>{node}</AnonymousGroupAuthProvider>;
}

beforeEach(() => {
  isAnonymousGroupAuthModeMock.mockReturnValue(true);
  routerReplaceMock.mockReset();
  fetchMock.mockReset();
  sessionStorage.clear();
});

async function importPage() {
  const mod = await import("@/app/group/page");
  return mod.default;
}

describe("/group page — mode gating", () => {
  it("renders the friendly fallback when NOT in anonymous-group mode", async () => {
    isAnonymousGroupAuthModeMock.mockReturnValue(false);
    const Page = await importPage();
    render(wrap(<Page />));
    expect(screen.getByText(/not available/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/group code/i)).not.toBeInTheDocument();
  });

  it("renders the form when in anonymous-group mode", async () => {
    isAnonymousGroupAuthModeMock.mockReturnValue(true);
    const Page = await importPage();
    render(wrap(<Page />));
    expect(screen.getByLabelText(/group code/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /join/i })).toBeInTheDocument();
  });
});

describe("/group page — happy join", () => {
  it("calls join + redirects to / on success", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        token: "t",
        uid: "anon-X-y",
        expires_at: Date.now() / 1000 + 3600,
      }),
    } as Response);
    const Page = await importPage();
    render(wrap(<Page />));

    fireEvent.change(screen.getByLabelText(/group code/i), {
      target: { value: "phys-7k2n" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /join/i }));
    });
    await waitFor(() => {
      expect(routerReplaceMock).toHaveBeenCalledWith("/");
    });
  });

  it("disables the button while joining", async () => {
    // Resolves never — keeps the provider in 'joining' state.
    fetchMock.mockImplementationOnce(() => new Promise(() => {}));
    const Page = await importPage();
    render(wrap(<Page />));

    fireEvent.change(screen.getByLabelText(/group code/i), {
      target: { value: "PHYS-7K2N" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /join/i }));
    });
    expect(screen.getByRole("button", { name: /joining/i })).toBeDisabled();
  });
});

describe("/group page — typed error rendering", () => {
  async function submitWith(status: number, body: unknown) {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status,
      json: async () => body,
    } as Response);
    const Page = await importPage();
    render(wrap(<Page />));
    fireEvent.change(screen.getByLabelText(/group code/i), {
      target: { value: "X-Y" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /join/i }));
    });
  }

  it("renders unknown_or_revoked message on 401", async () => {
    await submitWith(401, { detail: "group not found" });
    expect(
      await screen.findByText(/code not found, expired, or revoked/i),
    ).toBeInTheDocument();
  });

  it("renders rate-limited message with retry seconds on 429", async () => {
    await submitWith(429, { detail: "rate limit exceeded; retry after 7s" });
    expect(
      await screen.findByText(/try again in 7s/i),
    ).toBeInTheDocument();
  });

  it("renders at-capacity message on 503", async () => {
    await submitWith(503, { detail: "cap exceeded" });
    expect(
      await screen.findByText(/group is at capacity/i),
    ).toBeInTheDocument();
  });

  it("re-enables the Join button after an error (so user can retry)", async () => {
    await submitWith(401, { detail: "unknown" });
    // After error → status returns to 'idle', button no longer disabled
    // for non-empty inputs.
    expect(screen.getByRole("button", { name: /join/i })).not.toBeDisabled();
  });
});
