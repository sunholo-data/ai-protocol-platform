// M2 — A2UISurfaceMount tests
// The mount is the *layout primitive* that declares a named surface in the
// React tree. It binds its inner div ref into the SurfaceRegistry on mount
// and unregisters on unmount.
//
// ACTION-TRIGGER M2 (sprint 1.21) added two trailing test groups:
//   - `triggerOnAction={false}` (default): existing surface-action POST path
//   - `triggerOnAction={true}`: routes through useActionDrivenAgent instead

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import {
  SurfaceRegistryProvider,
  useSurfaceRegistry,
} from "@/providers/SurfaceRegistry";
import { subscribeSkillSwitchIntent } from "@/lib/skillSwitch";

function wrap(children: ReactNode) {
  return <SurfaceRegistryProvider>{children}</SurfaceRegistryProvider>;
}

describe("A2UISurfaceMount", () => {
  it("renders a div with data-surface attribute", () => {
    const { container } = render(
      wrap(<A2UISurfaceMount surfaceId="workspace" />),
    );
    const el = container.querySelector('[data-surface="workspace"]');
    expect(el).toBeTruthy();
    expect(el?.tagName).toBe("DIV");
  });

  it("forwards className to the underlying div", () => {
    const { container } = render(
      wrap(
        <A2UISurfaceMount surfaceId="workspace" className="w-1/2 bg-muted" />,
      ),
    );
    const el = container.querySelector('[data-surface="workspace"]');
    expect(el?.className).toBe("w-1/2 bg-muted");
  });

  it("registers itself with the SurfaceRegistry on mount; unregisters on unmount", () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }

    const { unmount } = render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" />
        </>,
      ),
    );

    // useLayoutEffect runs synchronously before paint — by the time render()
    // returns the registration is in place.
    expect(registryHandle).not.toBeNull();
    const mountRef = registryHandle!.getMount("workspace");
    expect(mountRef).not.toBeNull();
    expect(mountRef?.current).toBeInstanceOf(HTMLDivElement);
    expect(mountRef?.current?.getAttribute("data-surface")).toBe("workspace");

    unmount();
    // Unmount path runs the registry.unregister cleanup; can't query the
    // captured handle (provider gone), but render output is gone too.
  });

  it("returns null from getMount after the mount unmounts", () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }

    // Render with a parent provider so the registry survives the child unmount
    const { rerender } = render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" />
        </>,
      ),
    );

    expect(registryHandle!.getMount("workspace")).not.toBeNull();

    rerender(
      wrap(
        <>
          <Capture />
          {/* A2UISurfaceMount removed */}
        </>,
      ),
    );

    expect(registryHandle!.getMount("workspace")).toBeNull();
  });

  it("propagates a policy override to the registry (e.g., persistence=indefinite)", () => {
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
            surfaceId="sidebar"
            policy={{ persistence: "indefinite" }}
          />
        </>,
      ),
    );
    expect(registryHandle!.getPolicy("sidebar").persistence).toBe("indefinite");
    // Other fields keep their defaults
    expect(registryHandle!.getPolicy("sidebar").requiresUserGesture).toBe(false);
  });

  it("logs an error and refuses if two A2UISurfaceMounts share the same surfaceId", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" />
          <A2UISurfaceMount surfaceId="workspace" />
        </>,
      ),
    );

    // First mount wins; second logs an error but doesn't crash
    expect(registryHandle!.getMount("workspace")).not.toBeNull();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });
});

// ─── ACTION-TRIGGER M2: triggerOnAction prop branches ─────────────────────

// fetchWithAuth is module-level mocked so both the default `surface-action`
// POST path and the bundled `surface-action-run` (via useActionDrivenAgent)
// can be observed with the same spy. The hook itself is unit-tested in
// `src/hooks/__tests__/useActionDrivenAgent.test.tsx`; here we only assert
// the routing decision the mount makes based on `triggerOnAction`.
const fetchWithAuthSpy = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) =>
    fetchWithAuthSpy(...(args as [RequestInfo | URL, RequestInit?])),
}));

const triggerActionSpy = vi.fn();
vi.mock("@/hooks/useActionDrivenAgent", () => ({
  useActionDrivenAgent: () => ({ triggerAction: triggerActionSpy }),
}));

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

// Drive a real A2UI surface into the registry so `state.surface.onAction`
// has a SurfaceModel to subscribe to. The mount's effect needs a real
// SurfaceModel — not a mock — because the SurfaceRegistry's API constructs
// it through the v0.9 MessageProcessor.
//
// `basicCatalog.id` is the upstream URL ("https://a2ui.org/.../basic_catalog.json")
// — using a different string here would trip the SDK's "Catalog not found"
// guard. We re-export the id at the top of this block to keep tests
// future-proof against version bumps.
import { basicCatalog } from "@a2ui/react/v0_9";
const BASIC_CATALOG_ID = basicCatalog.id;

function pushSurface(
  registry: ReturnType<typeof useSurfaceRegistry>,
  surfaceId: string,
) {
  registry.appendMessages(
    surfaceId,
    [
      {
        version: "v0.9",
        createSurface: { surfaceId, catalogId: BASIC_CATALOG_ID },
      },
    ],
    `tc-${surfaceId}-${Math.random()}`,
  );
}

describe("A2UISurfaceMount — triggerOnAction prop (ACTION-TRIGGER M2)", () => {
  it("default (triggerOnAction omitted): clicks POST to the plain /surface-action endpoint — current behaviour preserved", async () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" sessionId="sess-1" />
        </>,
      ),
    );
    act(() => {
      pushSurface(registryHandle!, "workspace");
    });

    const surface = registryHandle!.getState("workspace")?.surface;
    expect(surface).toBeTruthy();

    await act(async () => {
      await surface!.dispatchAction({ event: { name: "click" } }, "btn-1");
    });

    await waitFor(() => {
      expect(fetchWithAuthSpy).toHaveBeenCalledOnce();
    });
    const [url, init] = fetchWithAuthSpy.mock.calls[0];
    expect(url).toBe("/api/proxy/api/sessions/sess-1/surface-action");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(init?.body as string);
    expect(body.surfaceId).toBe("workspace");
    expect(body.action.name).toBe("click");

    // triggerAction never invoked on the default branch.
    expect(triggerActionSpy).not.toHaveBeenCalled();
  });

  it("triggerOnAction={false} explicit: same as omitted — current behaviour preserved", async () => {
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
            surfaceId="workspace"
            sessionId="sess-1"
            triggerOnAction={false}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;
    await act(async () => {
      await surface.dispatchAction({ event: { name: "click" } }, "btn-1");
    });

    await waitFor(() => {
      expect(fetchWithAuthSpy).toHaveBeenCalledOnce();
    });
    expect(triggerActionSpy).not.toHaveBeenCalled();
  });

  it("triggerOnAction={true}: clicks route through useActionDrivenAgent.triggerAction — no direct POST to plain surface-action endpoint", async () => {
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
            surfaceId="workspace"
            sessionId="sess-1"
            skillId="skill-x"
            triggerOnAction={true}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;
    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "increment", context: { delta: 1 } } },
        "btn-1",
      );
    });

    await waitFor(() => {
      expect(triggerActionSpy).toHaveBeenCalledOnce();
    });
    const [calledSurfaceId, calledAction] = triggerActionSpy.mock.calls[0];
    expect(calledSurfaceId).toBe("workspace");
    expect(calledAction).toMatchObject({
      name: "increment",
      sourceComponentId: "btn-1",
      context: { delta: 1 },
    });

    // No plain surface-action POST when bundled endpoint is used.
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
  });

  it("NEVER SILENT (#8): a rejected run renders a VISIBLE error, not a silent grey surface", async () => {
    triggerActionSpy.mockRejectedValueOnce(new Error("The run was rejected (HTTP 500). Check the Activity tab or try again."));
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
            surfaceId="obligation_elicitation:leap"
            sessionId="sess-1"
            skillId="demo-ppa-expert"
            triggerOnAction={true}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "obligation_elicitation:leap"));
    const surface = registryHandle!.getState("obligation_elicitation:leap")?.surface!;
    await act(async () => {
      await surface.dispatchAction({ event: { name: "start_obligation_analysis" } }, "run-btn");
    });

    // The submit failed — the user MUST see it (data-testid + role=alert),
    // never a dead grey form.
    const alert = await screen.findByTestId("a2ui-surface-error");
    expect(alert).toHaveTextContent(/rejected|activity/i);
  });

  it("NEVER SILENT (#8): a completed run shows a persistent 'Sent' terminal badge (not a re-clickable dead button)", async () => {
    // triggerActionSpy resolves by default (success) — a launcher run whose
    // result renders in the Workbench, not a new chat surface.
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
            surfaceId="launch:obligation:1"
            sessionId="sess-1"
            skillId="demo-assistant"
            triggerOnAction={true}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "launch:obligation:1"));
    const surface = registryHandle!.getState("launch:obligation:1")?.surface!;
    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "start_obligation_analysis" } },
        "run-btn",
      );
    });

    // The click has an unmistakable, persistent consequence — not a silent
    // revert to a live button that reads as "nothing happened".
    const done = await screen.findByTestId("a2ui-surface-done");
    expect(done).toHaveTextContent(/sent/i);
  });

  it("confirm_delegation (8.2 full switch): emits a skill-switch intent and does NOT run surface-action", async () => {
    // Proceed on a handoff card is a SWITCH, not a surface-action-run: it hands
    // ChatShell the target skill id (to navigate on the same thread) and shows an
    // immediate working state — never a silent grey button.
    const intents: { targetSkillId: string }[] = [];
    const off = subscribeSkillSwitchIntent((i) => intents.push(i));
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    const { container } = render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount
            surfaceId="elicit:confirm_delegation:1"
            sessionId="sess-1"
            skillId="demo-assistant"
            triggerOnAction={true}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "elicit:confirm_delegation:1"));
    const surface = registryHandle!.getState("elicit:confirm_delegation:1")?.surface!;
    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "confirm_delegation", context: { target_skill_id: "ppa-obligation-uuid" } } },
        "proceed-btn",
      );
    });
    off();

    expect(intents).toEqual([{ targetSkillId: "ppa-obligation-uuid" }]);
    // The switch does NOT go through the surface-action-run path…
    expect(triggerActionSpy).not.toHaveBeenCalled();
    // …and shows an immediate working state (never-silent) while ChatShell navigates.
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it("VALUE FLOW (7.8): a TextField edit reaches readA2uiSurfaceState — the path elicitation assumptions travel to the tool", async () => {
    // The whole obligation elicitation depends on this: the user types into an
    // A2UI TextField (value bound to /contract_capacity_mw), and that value must
    // appear in readA2uiSurfaceState().[surfaceId].dataModel — because that is
    // what surface-action-run forwards to map_ppa_obligations as the assumption.
    const SID = "obligation_elicitation:leap";
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId={SID} sessionId="sess-1" skillId="demo-ppa-expert" triggerOnAction />
        </>,
      ),
    );
    act(() => {
      registryHandle!.appendMessages(
        SID,
        [
          { version: "v0.9", createSurface: { surfaceId: SID, catalogId: BASIC_CATALOG_ID } },
          {
            version: "v0.9",
            updateComponents: {
              surfaceId: SID,
              components: [
                { id: "root", component: "Column", children: ["cap"] },
                { id: "cap", component: "TextField", value: { path: "/contract_capacity_mw" }, label: "Contract Capacity (MW)" },
              ],
            },
          },
          { version: "v0.9", updateDataModel: { surfaceId: SID, value: { contract_capacity_mw: "" } } },
        ],
        `tc-${SID}`,
      );
    });

    // Type 100 into the capacity field.
    const input = (await screen.findByLabelText(/Contract Capacity/i)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "100" } });

    // The value MUST be in the snapshot the re-run forwards to the tool.
    await waitFor(() => {
      const snap = registryHandle!.readA2uiSurfaceState();
      const dm = snap[SID]?.dataModel as Record<string, unknown> | undefined;
      expect(dm?.contract_capacity_mw).toBe("100");
    });
  });

  it("triggerOnAction={true} but skillId missing: drops silently in dev — surface stays put, no POST, no triggerAction call", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
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
            surfaceId="workspace"
            sessionId="sess-1"
            triggerOnAction={true}
          />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;
    await act(async () => {
      await surface.dispatchAction({ event: { name: "click" } }, "btn-1");
    });

    expect(triggerActionSpy).not.toHaveBeenCalled();
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});

// ─── Per-action routing (tool-results-as-a2ui / 7.3 M3) ───────────────────────
// A `run:`-prefixed action name drives a full agent turn (surface-action-run)
// even on a default (fire-and-forget) mount, so one surface can mix client
// actions (a filter) with agent-run actions (a diff row's "Explain this diff").
describe("A2UISurfaceMount — per-action run routing (7.3 M3)", () => {
  it("run:-prefixed action routes through triggerAction even without triggerOnAction", async () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" sessionId="sess-1" skillId="ppa" />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;

    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "run:explain_diff", context: { clause: "Settlement Type" } } },
        "d-explain-1",
      );
    });

    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledOnce());
    const [surfaceId, action] = triggerActionSpy.mock.calls[0];
    expect(surfaceId).toBe("workspace");
    expect(action.name).toBe("run:explain_diff");
    // Not the fire-and-forget path.
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
  });

  it("plain (non-run:) action stays fire-and-forget even with skillId present", async () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" sessionId="sess-1" skillId="ppa" />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;

    await act(async () => {
      await surface.dispatchAction({ event: { name: "filter_clause" } }, "cp-1");
    });

    await waitFor(() => expect(fetchWithAuthSpy).toHaveBeenCalledOnce());
    expect(triggerActionSpy).not.toHaveBeenCalled();
  });

  it("chat:send action calls onChatMessage(prompt) — not surface-action or run", async () => {
    let registryHandle: ReturnType<typeof useSurfaceRegistry> | null = null;
    const onChatMessage = vi.fn();
    function Capture() {
      registryHandle = useSurfaceRegistry();
      return null;
    }
    render(
      wrap(
        <>
          <Capture />
          <A2UISurfaceMount surfaceId="workspace" sessionId="sess-1" skillId="ppa" onChatMessage={onChatMessage} />
        </>,
      ),
    );
    act(() => pushSurface(registryHandle!, "workspace"));
    const surface = registryHandle!.getState("workspace")?.surface!;

    await act(async () => {
      await surface.dispatchAction(
        { event: { name: "chat:send", context: { prompt: "Explain the Settlement Type difference." } } },
        "d-explain-1",
      );
    });

    await waitFor(() => expect(onChatMessage).toHaveBeenCalledWith("Explain the Settlement Type difference."));
    expect(fetchWithAuthSpy).not.toHaveBeenCalled();
    expect(triggerActionSpy).not.toHaveBeenCalled();
  });
});

// Wire fixture is produced by inbox_to_a2ui and validated with the Basic catalog.
import inboxMessages from "./fixtures/inbox-board.json";
describe("Inbox direct actions through the real SDK", () => {
  let registry: ReturnType<typeof useSurfaceRegistry>;
  function Capture() { registry = useSurfaceRegistry(); return null; }
  function showInbox() {
    render(wrap(<><Capture /><A2UISurfaceMount surfaceId="inbox_board" sessionId="inbox-thread" /></>));
    act(() => registry.appendMessages("inbox_board", structuredClone(inboxMessages) as any, "listing-1", { kind: "inbox_board", title: "Inbox" }));
  }

  it("opens the original task thread and its skill through the real Basic catalog", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    showInbox();
    expect(screen.getByText(/Messages to worker/)).toBeInTheDocument();
    expect(screen.getByText(/Mailbox: unread/)).toBeInTheDocument();
    fetchWithAuthSpy.mockResolvedValue(new Response(JSON.stringify({ status: "ok", message: "Opened #1.", messages: [] }), { status: 200 }));
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    fireEvent.click(screen.getByRole("button", { name: "Open related thread" }));
    expect(open).toHaveBeenLastCalledWith(new URL("/chat/web-researcher?session=thread-worker-abcdef", window.location.href).href, "_blank", "noopener,noreferrer");
    fireEvent.click(screen.getByRole("button", { name: "Open skill in a new chat" }));
    expect(open).toHaveBeenLastCalledWith(new URL("/chat/web-researcher", window.location.href).href, "_blank", "noopener,noreferrer");
    expect(triggerActionSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("a2ui-surface-direct-status")).toHaveTextContent("Opened #1."));
    open.mockRestore();
  });

  it("resolves checkbox selection, reports pending and updates the result without a model run", async () => {
    let finish!: (response: Response) => void;
    fetchWithAuthSpy.mockImplementation(() => new Promise<Response>(resolve => { finish = resolve; }));
    showInbox();
    fireEvent.click(screen.getByRole("checkbox", { name: "#1 Review plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));
    await waitFor(() => expect(fetchWithAuthSpy).toHaveBeenCalledOnce());
    const [url, init] = fetchWithAuthSpy.mock.calls[0];
    expect(url).toContain("/inbox-thread/surface-action-direct");
    expect(JSON.parse(init.body).action.context.selected).toEqual({ "m1": true });
    expect(screen.getByTestId("a2ui-surface-running")).toBeInTheDocument();
    const model = { items: [{ key: "m1", ref: "#1", title: "Review plan", status: "read" }], selected: { "m1": false } };
    await act(async () => finish(new Response(JSON.stringify({
      status: "ok", message: "1 message marked read.", messages: [{
          version: "v0.9", updateDataModel: { surfaceId: "inbox_board", value: model },
        }],
    }), { status: 200 })));
    expect(await screen.findByRole("status")).toHaveTextContent("1 message marked read.");
    expect(registry.readA2uiSurfaceState().inbox_board.dataModel).toEqual(model);
    expect(triggerActionSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("a2ui-surface-running")).not.toBeInTheDocument();
  });

  it("shows server refusals and keeps the selected message available for retry", async () => {
    fetchWithAuthSpy.mockResolvedValue(new Response(JSON.stringify({ detail: "This inbox is unavailable." }), { status: 403 }));
    showInbox();
    fireEvent.click(screen.getByRole("checkbox", { name: "#1 Review plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));
    expect(await screen.findByTestId("a2ui-surface-direct-status")).toHaveTextContent("This inbox is unavailable.");
    expect(screen.getByRole("checkbox", { name: "#1 Review plan" })).toBeChecked();
    expect(triggerActionSpy).not.toHaveBeenCalled();
  });
});


describe("Inbox reader and selection feedback", () => {
  it("renders safe Markdown in a dialog and leaves the list compact", async () => {
    let registry!: ReturnType<typeof useSurfaceRegistry>;
    function Capture() { registry = useSurfaceRegistry(); return null; }
    fetchWithAuthSpy.mockResolvedValue(new Response(JSON.stringify({status: "ok", message: "Opened #1.", messages: [{
      version: "v0.9", updateComponents: {surfaceId: "inbox_board", components: [{id: "ib-body-m1", component: "Text", variant: "body", text: "## Report\n\n- **Ready**\n- Review next\n\n```js\nconst done = true;\n```\n\n<img src='https://example.com/track'>\n\n![private image](https://example.com/track)"}]},
    }]}), {status:200}));
    render(wrap(<><Capture /><A2UISurfaceMount surfaceId="inbox_board" sessionId="inbox-thread" /></>));
    act(() => registry.appendMessages("inbox_board", structuredClone(inboxMessages) as any, "inbox-fixture"));
    fireEvent.click(screen.getByRole("button", {name:"Open"}));
    expect(await screen.findByRole("heading", {name:"Report"})).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Ready").tagName).toBe("STRONG");
    expect(screen.getByText("const done = true;").tagName).toBe("CODE");
    expect(document.querySelector("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name:"Close details"}));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/Plan ready for review/)).toBeInTheDocument();
  });

  it("guards empty AI actions and shows the current selection count", async () => {
    let registry!: ReturnType<typeof useSurfaceRegistry>;
    function Capture() { registry = useSurfaceRegistry(); return null; }
    const send = vi.fn();
    render(wrap(<><Capture /><A2UISurfaceMount surfaceId="inbox_board" sessionId="inbox-thread" onChatMessage={send} /></>));
    act(() => registry.appendMessages("inbox_board", structuredClone(inboxMessages) as any, "inbox-fixture"));
    fireEvent.click(screen.getByRole("button", {name:"Summarise these"}));
    expect(await screen.findByText("Select one or more messages first.")).toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", {name:"#1 Review plan"}));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"Summarise these"}));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());
  });
});
