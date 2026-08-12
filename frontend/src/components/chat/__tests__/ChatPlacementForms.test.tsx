// ChatPlacementForms — the obligation ELICITATION form (7.8 M1) rendered inline
// in the chat thread.
//
// A `map_ppa_obligations` refusal on a template contract is mapped server-side
// to a multi-field A2UI form surface with `artifact.placement === "chat"`.
// ChatPlacementForms discovers it via `useArtifacts()` and mounts it with
// `triggerOnAction`, so its "Run the analysis" button drives a full
// surface-action-run turn. These tests drive a REAL A2UI v0.9 surface into the
// registry (via appendMessages) and assert:
//   - only chat-placement artifacts render here (workbench ones don't);
//   - the button's start_obligation_analysis action routes to triggerAction
//     (surface-action-run), carrying the doc identity;
//   - the filled field values ride the surface data model into the snapshot
//     triggerAction sends (readA2uiSurfaceState) — the authoritative,
//     no-LLM-transcription channel for the trust-critical numbers;
//   - the never-silent path: blank required fields still fire a run (backend
//     re-refuses and re-emits the form) AND the guidance message renders.

import { act, render, waitFor, within } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { basicCatalog } from "@a2ui/react/v0_9";
import { ChatPlacementForms } from "@/components/chat/ChatPlacementForms";
import {
  type A2uiArtifact,
  SurfaceRegistryProvider,
  useSurfaceRegistry,
} from "@/providers/SurfaceRegistry";

const BASIC_CATALOG_ID = basicCatalog.id;

// The surface-action-run dispatch is exercised via the real A2UISurfaceMount →
// useActionDrivenAgent.triggerAction; mock the hook so we observe the routing
// decision (the hook has its own end-to-end tests).
const triggerActionSpy = vi.fn();
vi.mock("@/hooks/useActionDrivenAgent", () => ({
  useActionDrivenAgent: () => ({ triggerAction: triggerActionSpy }),
}));

const fetchWithAuthSpy = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) =>
    fetchWithAuthSpy(...(args as [RequestInfo | URL, RequestInit?])),
}));

beforeEach(() => {
  triggerActionSpy.mockReset();
  triggerActionSpy.mockResolvedValue(undefined);
  fetchWithAuthSpy.mockReset();
  fetchWithAuthSpy.mockResolvedValue(new Response(null, { status: 204 }) as Response);
});

afterEach(() => {
  vi.clearAllMocks();
});

const FORM_SURFACE = "obligation_elicitation:demo-leap";
const NEVER_SILENT_NOTE =
  "Click Run the analysis. If a required field (*) is blank the form re-appears with a note — the run never fails silently.";
const CHAT_ARTIFACT: A2uiArtifact = {
  kind: "obligation-elicitation-form",
  title: "Complete the analysis",
  placement: "chat",
};

// Mirrors the backend obligation_elicitation_form_to_a2ui output (retargeted to
// the form surface): two DateTimeInputs (dates), numeric TextFields (amounts),
// a submit Button firing start_obligation_analysis with the flat doc identity,
// and an updateDataModel seeding every bound path.
function formMessages(surfaceId: string, doc = "demo-leap"): Record<string, unknown>[] {
  return [
    { version: "v0.9", createSurface: { surfaceId, catalogId: BASIC_CATALOG_ID } },
    {
      version: "v0.9",
      updateComponents: {
        surfaceId,
        components: [
          { id: "h", component: "Text", text: "Complete the obligation analysis", variant: "h3" },
          { id: "eff-l", component: "Text", text: "Contract effective / start date *", variant: "h5" },
          { id: "eff", component: "DateTimeInput", value: { path: "/effective_date" }, enableDate: true, enableTime: false, label: "Contract effective / start date" },
          { id: "cod-l", component: "Text", text: "Guaranteed Commercial Operation Date (COD) *", variant: "h5" },
          { id: "cod", component: "DateTimeInput", value: { path: "/cod_date" }, enableDate: true, enableTime: false, label: "COD" },
          { id: "cap-l", component: "Text", text: "Contract Capacity (MW) *", variant: "h5" },
          { id: "cap", component: "TextField", value: { path: "/contract_capacity_mw" }, validationRegexp: "^[0-9,]*$", label: "Contract Capacity" },
          { id: "price-l", component: "Text", text: "COD milestone price / amount (EUR) *", variant: "h5" },
          { id: "price", component: "TextField", value: { path: "/contract_price" }, validationRegexp: "^[0-9,]*$", label: "Price" },
          { id: "note", component: "Text", text: NEVER_SILENT_NOTE, variant: "h5" },
          { id: "btn-label", component: "Text", text: "Run the analysis" },
          {
            id: "btn",
            component: "Button",
            child: "btn-label",
            variant: "primary",
            action: { event: { name: "start_obligation_analysis", context: { doc } } },
          },
          {
            id: "root",
            component: "Column",
            children: ["h", "eff-l", "eff", "cod-l", "cod", "cap-l", "cap", "price-l", "price", "note", "btn"],
          },
        ],
      },
    },
    {
      version: "v0.9",
      updateDataModel: {
        surfaceId,
        value: { effective_date: "", cod_date: "", contract_capacity_mw: "", contract_price: "" },
      },
    },
  ];
}

function wrap(children: ReactNode) {
  return <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>;
}

/** Render ChatPlacementForms and return the captured registry handle. */
function renderForms() {
  let registry: ReturnType<typeof useSurfaceRegistry> | null = null;
  function Capture() {
    registry = useSurfaceRegistry();
    return null;
  }
  const utils = render(
    wrap(
      <>
        <Capture />
        <ChatPlacementForms sessionId="sess-1" skillId="one-ppa-expert" />
      </>,
    ),
  );
  return { ...utils, getRegistry: () => registry! };
}

describe("ChatPlacementForms", () => {
  it("renders nothing until a chat-placement artifact arrives", () => {
    const { queryByTestId } = renderForms();
    expect(queryByTestId("chat-placement-forms")).toBeNull();
  });

  it("does NOT render a workbench-placement artifact (only placement:chat)", () => {
    const { getRegistry, queryByTestId } = renderForms();
    act(() => {
      getRegistry().appendMessages(FORM_SURFACE, formMessages(FORM_SURFACE), "tc-1", {
        kind: "obligation-refusal",
        placement: "workbench",
      });
    });
    expect(queryByTestId("chat-placement-forms")).toBeNull();
  });

  it("mounts the chat form surface and shows the never-silent guidance note", () => {
    const { getRegistry, getByTestId, container } = renderForms();
    act(() => {
      getRegistry().appendMessages(FORM_SURFACE, formMessages(FORM_SURFACE), "tc-1", CHAT_ARTIFACT);
    });

    const wrapEl = getByTestId("chat-placement-forms");
    expect(within(wrapEl).getByText(NEVER_SILENT_NOTE)).toBeTruthy();
    // The generic A2UISurfaceMount is mounted for the form surface.
    expect(container.querySelector(`[data-surface="${FORM_SURFACE}"]`)).toBeTruthy();
  });

  it("the Run button routes start_obligation_analysis to surface-action-run (triggerAction), carrying the flat doc identity", async () => {
    const { getRegistry } = renderForms();
    act(() => {
      getRegistry().appendMessages(FORM_SURFACE, formMessages(FORM_SURFACE), "tc-1", CHAT_ARTIFACT);
    });
    const surface = getRegistry().getState(FORM_SURFACE)?.surface;
    expect(surface).toBeTruthy();

    await act(async () => {
      await surface!.dispatchAction(
        { event: { name: "start_obligation_analysis", context: { doc: "demo-leap" } } },
        "btn",
      );
    });

    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledOnce());
    const [calledSurface, action] = triggerActionSpy.mock.calls[0];
    expect(calledSurface).toBe(FORM_SURFACE);
    expect(action.name).toBe("start_obligation_analysis");
    expect(action.context).toEqual({ doc: "demo-leap" });
    // Not the fire-and-forget path.
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
  });

  it("the filled field values ride the surface data model into the snapshot triggerAction sends", () => {
    const { getRegistry } = renderForms();
    const registry = getRegistry();
    act(() => {
      registry.appendMessages(FORM_SURFACE, formMessages(FORM_SURFACE), "tc-1", CHAT_ARTIFACT);
    });
    // User fills the fields → the inputs write to the bound data model.
    act(() => {
      registry.appendMessages(
        FORM_SURFACE,
        [
          {
            version: "v0.9",
            updateDataModel: {
              surfaceId: FORM_SURFACE,
              value: {
                effective_date: "2026-01-01",
                cod_date: "2027-07-01",
                contract_capacity_mw: "100",
                contract_price: "250000",
              },
            },
          },
        ],
        "tc-fill",
      );
    });

    // triggerAction reads exactly this snapshot for forwardedProps.a2ui_surface_state —
    // the authoritative, no-LLM-transcription channel for the numbers.
    const snapshot = registry.readA2uiSurfaceState();
    const dm = snapshot[FORM_SURFACE].dataModel as Record<string, string>;
    expect(dm.contract_capacity_mw).toBe("100");
    expect(dm.contract_price).toBe("250000");
    expect(dm.effective_date).toBe("2026-01-01");
    expect(dm.cod_date).toBe("2027-07-01");
  });

  it("append-only: an earlier form FREEZES (Submitted) once a newer form appends", () => {
    const S1 = "obligation_elicitation:demo-leap:1";
    const S2 = "obligation_elicitation:demo-leap:2";
    const { getRegistry, container } = renderForms();
    act(() => {
      getRegistry().appendMessages(S1, formMessages(S1), "tc-1", CHAT_ARTIFACT);
    });
    // Single form → active, not frozen.
    expect(container.querySelector(`[data-chat-form="${S1}"]`)?.getAttribute("data-submitted")).toBeNull();

    // A re-refusal appends a SECOND form (unique surface via elicit_seq).
    act(() => {
      getRegistry().appendMessages(S2, formMessages(S2), "tc-2", CHAT_ARTIFACT);
    });
    // First form is now a frozen submitted record; the second is active.
    expect(container.querySelector(`[data-chat-form="${S1}"]`)?.getAttribute("data-submitted")).toBe("true");
    expect(container.querySelector(`[data-chat-form="${S2}"]`)?.getAttribute("data-submitted")).toBeNull();
    // Both remain in the transcript (append-only, prior submission stays visible).
    expect(container.querySelectorAll('[data-testid="chat-placement-forms"] > div')).toHaveLength(2);
  });

  it("a REPLAYED confirm/elicitation card renders frozen (never a live Proceed)", () => {
    // v6.10.0: a handoff confirm card rehydrated from history was already acted
    // on / the session moved on (the switch navigated away). It must render as a
    // frozen Submitted record, not a live "Proceed" the user can re-fire.
    const S1 = "elicit:confirm_delegation:1";
    const { getRegistry, container } = renderForms();
    act(() => {
      getRegistry().appendMessages(S1, formMessages(S1), "tc-1", {
        kind: "elicitation",
        elicitationKind: "confirm",
        placement: "chat",
        replayed: true,
      });
    });
    // Even as the ONLY/last surface, a replayed card is frozen.
    expect(container.querySelector(`[data-chat-form="${S1}"]`)?.getAttribute("data-submitted")).toBe("true");
  });

  it("a settlement SUMMARY after a form freezes the form; the summary itself is not a form (no badge)", () => {
    const S1 = "obligation_elicitation:demo-leap:1";
    const SUMMARY = "obligation_analysis:demo-leap";
    const { getRegistry, container } = renderForms();
    act(() => {
      getRegistry().appendMessages(S1, formMessages(S1), "tc-1", CHAT_ARTIFACT);
      getRegistry().appendMessages(SUMMARY, formMessages(SUMMARY), "tc-2", {
        kind: "obligation-analysis",
        title: "Obligation Analysis",
        placement: "chat",
      });
    });
    // The form froze (a newer surface exists); the analysis summary is not a
    // form, so it never carries the Submitted badge.
    expect(container.querySelector(`[data-chat-form="${S1}"]`)?.getAttribute("data-submitted")).toBe("true");
    expect(container.querySelector(`[data-chat-form="${SUMMARY}"]`)?.getAttribute("data-submitted")).toBeNull();
  });

  it("never-silent on missing required: clicking Run with blanks still fires a run (backend re-refuses, form re-appears)", async () => {
    const { getRegistry } = renderForms();
    act(() => {
      getRegistry().appendMessages(FORM_SURFACE, formMessages(FORM_SURFACE), "tc-1", CHAT_ARTIFACT);
    });
    // Fields still seeded empty (untouched).
    const snapshot = getRegistry().readA2uiSurfaceState();
    expect((snapshot[FORM_SURFACE].dataModel as Record<string, string>).contract_capacity_mw).toBe("");

    const surface = getRegistry().getState(FORM_SURFACE)?.surface!;
    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "start_obligation_analysis", context: { doc: "demo-leap" } } },
        "btn",
      );
    });
    // A run STILL fires — not a silent no-op; the tool refuses again and the
    // form is re-emitted. The always-visible guidance note is the ask.
    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledOnce());
  });
});
