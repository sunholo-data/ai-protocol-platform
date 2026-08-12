// v6.18.0 Gap B — the gate must swap the whole UI for the "access restricted"
// screen when a DOMAIN_NOT_PERMITTED event fires, show the signed-in email, and
// offer sign-out; and it must clear once the user signs out.

import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessRestrictedGate } from "@/components/auth/AccessRestrictedGate";
import { DOMAIN_NOT_PERMITTED_EVENT } from "@/lib/apiClient";

const signOut = vi.fn(async () => {});
let mockUser: { email: string | null } | null = { email: "x@gmail.com" };

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, signOut }),
}));

afterEach(() => {
  signOut.mockClear();
  mockUser = { email: "x@gmail.com" };
});

function fireDenied(message?: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent(DOMAIN_NOT_PERMITTED_EVENT, { detail: { message } }));
  });
}

describe("AccessRestrictedGate", () => {
  it("renders children until a domain-rejection event fires", () => {
    render(
      <AccessRestrictedGate>
        <div>app content</div>
      </AccessRestrictedGate>,
    );
    expect(screen.getByText("app content")).toBeTruthy();

    fireDenied();
    expect(screen.queryByText("app content")).toBeNull();
    expect(screen.getByText(/access restricted/i)).toBeTruthy();
    expect(screen.getByText("x@gmail.com")).toBeTruthy();
  });

  it("sign-out button invokes signOut", () => {
    render(
      <AccessRestrictedGate>
        <div>app content</div>
      </AccessRestrictedGate>,
    );
    fireDenied();
    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("clears the overlay once the user signs out (user → null)", () => {
    const { rerender } = render(
      <AccessRestrictedGate>
        <div>app content</div>
      </AccessRestrictedGate>,
    );
    fireDenied();
    expect(screen.getByText(/access restricted/i)).toBeTruthy();

    mockUser = null; // simulate Firebase sign-out
    rerender(
      <AccessRestrictedGate>
        <div>app content</div>
      </AccessRestrictedGate>,
    );
    expect(screen.queryByText(/access restricted/i)).toBeNull();
    expect(screen.getByText("app content")).toBeTruthy();
  });
});
