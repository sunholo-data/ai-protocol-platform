import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SurfaceRegistryProvider, useSurfaceRegistry } from "@/providers/SurfaceRegistry";
import { DirectLaunchers } from "../DirectLaunchers";

const fetchMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: fetchMock }));
let registry: ReturnType<typeof useSurfaceRegistry>;
function Capture() { registry = useSurfaceRegistry(); return null; }
const entries = [{ label: "Waiting for you", name: "fixed_board", tool: "list_board" }];
function App({ sessionId = "fresh" }: { sessionId?: string }) {
  return <SurfaceRegistryProvider><Capture /><DirectLaunchers skillId="skill" sessionId={sessionId} launchers={entries} /></SurfaceRegistryProvider>;
}
beforeEach(() => fetchMock.mockReset());

it("opens a fixed board without a model turn or browser-supplied tool arguments", async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({
    status: "ok", message: "Opened.", surface_id: "board", artifact: { kind: "board", title: "Waiting for you" },
    messages: [{ version: "v0.9", createSurface: { surfaceId: "board", catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json" } }],
  }) });
  render(<App />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Waiting for you" })));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/proxy/api/skills/skill/sessions/fresh/surface-launch-direct");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "fixed_board" });
  expect(registry!.listArtifacts()[0].surfaceId).toBe("board");
  expect(screen.getByRole("status")).toHaveTextContent("Opened.");
});

it("shows immediate work and disabled-service/refusal errors visibly", async () => {
  let resolve!: (v: unknown) => void;
  fetchMock.mockReturnValue(new Promise(r => { resolve = r; }));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Waiting for you" }));
  expect(screen.getByRole("status")).toHaveTextContent("Opening");
  expect(screen.getByRole("button")).toBeDisabled();
  await act(async () => resolve({ ok: false, status: 503, json: async () => ({ detail: "Approval service is disabled" }) }));
  expect(screen.getByRole("alert")).toHaveTextContent("Approval service is disabled");
});

it("ignores a board response for a session the owner has left", async () => {
  let resolve!: (v: unknown) => void;
  fetchMock.mockReturnValue(new Promise(r => { resolve = r; }));
  const view = render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Waiting for you" }));
  view.rerender(<App sessionId="another" />);
  await act(async () => resolve({ ok: true, json: async () => ({
    status: "ok", surface_id: "board", artifact: { kind: "board" },
    messages: [{ version: "v0.9", createSurface: { surfaceId: "board", catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json" } }],
  }) }));
  expect(registry!.getState("board")).toBeNull();
});
