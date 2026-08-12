// ACTION-TRIGGER M3.1 — /dev/a2ui fixture page tests.
//
// The page hand-seeds a Button-with-counter surface so the visual
// rendering is deterministic without a live agent run. Tests assert:
//   (1) the Pattern 1 section renders, with the surface mount inside;
//   (2) dispatching the seeded Button's action routes through the
//       useActionDrivenAgent hook with the contract-pinned surface id +
//       action name (counter-main / increment).
//
// We don't render through the click-handling DOM path because the SDK's
// `<A2uiSurface>` translates clicks via per-catalog component renderers;
// the contract that matters for this sprint is the action body the
// SurfaceModel produces. So we drive the action via `dispatchAction`
// — same path the Button's onClick uses inside the SDK — and assert the
// outer mount routes it through `useActionDrivenAgent`. Same pattern the
// existing `A2UISurfaceMount.test.tsx` uses for its triggerOnAction
// branches.

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// fetchWithAuth is mocked at module scope so the SurfaceRegistry's
// outbound POST (it doesn't make one on the action-triggered branch, but
// belt-and-braces) AND any incidental network calls land on the spy
// rather than hitting the test network. Mirrors the pattern from
// `frontend/src/components/protocols/__tests__/A2UISurfaceMount.test.tsx`.
const fetchWithAuthSpy = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) =>
    fetchWithAuthSpy(...(args as [RequestInfo | URL, RequestInit?])),
}));

const triggerActionSpy = vi.fn();
vi.mock("@/hooks/useActionDrivenAgent", () => ({
  useActionDrivenAgent: () => ({ triggerAction: triggerActionSpy }),
}));

import { useSurfaceRegistry } from "@/providers/SurfaceRegistry";
import A2uiDevPage from "../page";

beforeEach(() => {
  fetchWithAuthSpy.mockReset();
  fetchWithAuthSpy.mockResolvedValue(
    new Response(null, { status: 204 }) as Response,
  );
  triggerActionSpy.mockReset();
  triggerActionSpy.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("/dev/a2ui Pattern 1 — Click-Driven section", () => {
  it("renders the page heading and the Pattern 1 section", () => {
    render(<A2uiDevPage />);
    expect(screen.getByText(/A2UI fixture playground/i)).toBeInTheDocument();
    const section = screen.getByTestId("pattern1-section");
    expect(section).toBeInTheDocument();
    expect(section.textContent).toContain("Pattern 1 — Click-Driven AI UI");
    // The Pattern 1 surface-action-run URL should be visible to the user
    // (so they know where the click POSTs) — also acts as a contract
    // guard so a typo in the URL doesn't silently slip past.
    expect(section.textContent).toContain(
      "/api/skills/demo-click-counter/sessions/pattern1-fixture-001/surface-action-run",
    );
  });

  it("contains the A2UISurfaceMount for `counter-main`", () => {
    render(<A2uiDevPage />);
    // The mount renders `<div data-surface={surfaceId}>` regardless of
    // whether the seed effect has produced a SurfaceModel yet, so this
    // assertion is stable against effect-ordering.
    const mountEl = document.querySelector('[data-surface="counter-main"]');
    expect(mountEl).not.toBeNull();
    expect(mountEl?.tagName).toBe("DIV");
  });

  it("renders the contract-pinned surface mount inside the Pattern 1 section (skillId, sessionId, surfaceId visible in DOM/copy)", () => {
    render(<A2uiDevPage />);
    // The mount is inside the Pattern 1 section.
    const section = screen.getByTestId("pattern1-section");
    const surfaceDiv = section.querySelector('[data-surface="counter-main"]');
    expect(surfaceDiv).not.toBeNull();
    // skillId is part of the URL hint copy directly under the mount — if
    // we ever rename the skill, this guard catches it.
    expect(section.textContent).toContain("demo-click-counter");
    expect(section.textContent).toContain("pattern1-fixture-001");
  });
});

// Because the page wraps its own SurfaceRegistryProvider and doesn't
// accept providers from above, the cleanest way to drive the seeded
// surface's action is to mount the SAME provider + a registry probe in
// a sibling test. This second describe block does exactly that — it
// imports the page's seed payload + contract constants by importing
// the page module (which exports the surface id via the rendered DOM
// `data-surface` attribute we already asserted on above). We seed the
// surface ourselves with the same shape and then drive the action via
// dispatchAction, matching the path the SDK Button takes.

import { type ReactNode } from "react";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { SurfaceRegistryProvider } from "@/providers/SurfaceRegistry";
import { basicCatalog } from "@a2ui/react/v0_9";

const PATTERN1_SURFACE_ID = "counter-main";
const PATTERN1_SKILL_ID = "demo-click-counter";
const PATTERN1_SESSION_ID = "pattern1-fixture-001";

const PATTERN1_SEED_MESSAGES: Record<string, unknown>[] = [
  {
    version: "v0.9",
    createSurface: {
      surfaceId: PATTERN1_SURFACE_ID,
      catalogId: basicCatalog.id,
      root: "root",
      components: [
        { id: "root", component: "Column", children: ["btn"] },
        {
          id: "btn",
          component: "Button",
          child: "btn-label",
          action: { event: { name: "increment", context: {} } },
        },
        { id: "btn-label", component: "Text", text: "Click me" },
      ],
    },
  },
];

function wrap(children: ReactNode) {
  return <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>;
}

describe("/dev/a2ui Pattern 1 — increment action dispatch contract", () => {
  it("dispatching the Button action with the page's contract ids routes through useActionDrivenAgent.triggerAction('counter-main', {name:'increment',...})", async () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount
            surfaceId={PATTERN1_SURFACE_ID}
            skillId={PATTERN1_SKILL_ID}
            sessionId={PATTERN1_SESSION_ID}
            triggerOnAction={true}
          />
        </>,
      ),
    );

    act(() => {
      registryHandle!.appendMessages(
        PATTERN1_SURFACE_ID,
        PATTERN1_SEED_MESSAGES,
        "test-seed-1",
      );
    });

    const surface = registryHandle!.getState(PATTERN1_SURFACE_ID)?.surface;
    expect(surface).toBeTruthy();

    await act(async () => {
      // Same payload shape the SDK Button passes through its onClick
      // (see M2's note in the sprint brief — payload is wrapped in
      // `{event: {name, context}}`, NOT a bare {name, context}).
      await surface!.dispatchAction(
        { event: { name: "increment", context: {} } },
        "btn",
      );
    });

    await waitFor(() => {
      expect(triggerActionSpy).toHaveBeenCalledOnce();
    });

    const [calledSurfaceId, calledAction] = triggerActionSpy.mock.calls[0];
    expect(calledSurfaceId).toBe(PATTERN1_SURFACE_ID);
    expect(calledAction).toMatchObject({
      name: "increment",
      sourceComponentId: "btn",
    });

    // triggerOnAction=true → no fire-and-forget POST to /surface-action.
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
  });
});
