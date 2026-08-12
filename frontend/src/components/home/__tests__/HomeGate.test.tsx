import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { LandingTarget } from "@/hooks/useLandingTarget";

const replace = vi.fn();
let authState: { user: unknown; loading: boolean } = { user: null, loading: false };
let landing: LandingTarget = { kind: "loading" };

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => authState }));
vi.mock("@/hooks/useLandingTarget", () => ({ useLandingTarget: () => landing }));

import { HomeGate } from "@/components/home/HomeGate";

function renderGate() {
  return render(
    <HomeGate>
      <div data-testid="landing">landing</div>
    </HomeGate>,
  );
}

describe("HomeGate", () => {
  beforeEach(() => {
    replace.mockReset();
    authState = { user: null, loading: false };
    landing = { kind: "loading" };
  });

  it("renders the landing for logged-out users and never redirects", () => {
    authState = { user: null, loading: false };
    renderGate();
    expect(screen.getByTestId("landing")).not.toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("renders the landing while auth is still loading", () => {
    authState = { user: null, loading: true };
    renderGate();
    expect(screen.getByTestId("landing")).not.toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects a logged-in user to their resumed chat", () => {
    authState = { user: { uid: "u1" }, loading: false };
    landing = { kind: "resume", href: "/chat/@aitana-platform/ppa-expert?session=s1" };
    renderGate();
    expect(replace).toHaveBeenCalledWith("/chat/@aitana-platform/ppa-expert?session=s1");
    expect(screen.queryByTestId("landing")).toBeNull();
    expect(screen.getByTestId("home-gate-redirecting")).not.toBeNull();
  });

  it("redirects a logged-in user with no history to a fresh primary chat", () => {
    authState = { user: { uid: "u1" }, loading: false };
    landing = { kind: "fresh", href: "/chat/@aitana-platform/ppa-expert" };
    renderGate();
    expect(replace).toHaveBeenCalledWith("/chat/@aitana-platform/ppa-expert");
  });

  it("falls back to the landing for a logged-in user with nothing to route to", () => {
    authState = { user: { uid: "u1" }, loading: false };
    landing = { kind: "landing" };
    renderGate();
    expect(screen.getByTestId("landing")).not.toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("holds (no landing flash) while resolving for a logged-in user", () => {
    authState = { user: { uid: "u1" }, loading: false };
    landing = { kind: "loading" };
    renderGate();
    expect(screen.queryByTestId("landing")).toBeNull();
    expect(screen.getByTestId("home-gate-redirecting")).not.toBeNull();
  });
});
