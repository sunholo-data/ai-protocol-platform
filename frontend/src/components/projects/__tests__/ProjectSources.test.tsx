import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { SYNC_ANSWER_TIMEOUT_MS } from "@/hooks/useProjectSources";
import { PROGRESS_POLL_MS, ProjectSources } from "../ProjectSources";

const mockFetch = vi.mocked(fetchWithAuth);
const P = "/api/proxy/api/projects/p1";

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

type Handler = (init?: RequestInit) => Response | Promise<Response>;
let handlers: Record<string, Handler | Handler[]>;
let calls: { key: string; body: unknown }[];

const github = {
  id: "s1",
  type: "github",
  repo: "example-org/clients",
  ref: "main",
  path: "client-a",
  folderPrefix: "client-a",
  status: "ok",
  syncedCommit: "22e2b27abcdef",
  syncedAt: new Date().toISOString(),
  lastReport: null,
};

const report = (over: Record<string, unknown> = {}) => ({
  status: "ok",
  counts: { added: 2, updated: 1, unchanged: 40, skipped: 0, failed: 0, remaining: 0 },
  ...over,
});

beforeEach(() => {
  mockFetch.mockReset();
  calls = [];
  handlers = {
    [`GET ${P}/sources`]: () => res(200, { sources: [github], credentials: [{ ref: "clients", repos: ["example-org/clients"] }] }),
  };
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const h = handlers[key];
    if (!h) throw new Error(`unexpected ${key}`);
    if (Array.isArray(h)) return (h.length > 1 ? h.shift()! : h[0])(init);
    return h(init);
  });
});

describe("ProjectSources", () => {
  it("lists a source with where it syncs from and when", async () => {
    render(<ProjectSources projectId="p1" />);
    const row = await screen.findByTestId("project-source");
    expect(within(row).getByText("example-org/clients/client-a (main) → client-a/")).toBeTruthy();
    expect(within(row).getByText(/Up to date at 22e2b27 · checked just now/)).toBeTruthy();
  });

  it("shows a sync as pending, then its outcome", async () => {
    let release: (r: Response) => void = () => undefined;
    handlers[`POST ${P}/sources/s1/sync`] = () => new Promise<Response>((r) => (release = r));
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(screen.getByRole("button", { name: "Syncing…" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Syncing/);
    release(res(200, report()));
    expect(await screen.findByText("Synced: 2 added · 1 updated · 40 unchanged.")).toBeTruthy();
  });

  it("continues a sync that stopped at the time limit", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = [
      () => res(200, report({ status: "partial", counts: { added: 100, remaining: 41 } })),
      () => res(200, report({ counts: { added: 41, remaining: 0 } })),
    ];
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Synced: 41 added.")).toBeTruthy();
    expect(calls.filter((c) => c.key === `POST ${P}/sources/s1/sync`)).toHaveLength(2);
  });

  it("holds a mass removal until it is confirmed", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = [
      () =>
        res(200, report({ status: "needs_review", message: "12 files would be removed.", removalsHeld: ["a.md", "b.md"] })),
      () => res(200, report({ counts: { removed: 2 } })),
    ];
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText(/more than the safety limit/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Remove them" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.key === `POST ${P}/sources/s1/sync`).at(-1)?.body).toMatchObject({
        confirm_removals: true,
      }),
    );
    expect(await screen.findByText("Synced: 2 removed.")).toBeTruthy();
  });

  it("says why a sync failed", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = () =>
      res(200, report({ status: "failed", error: { message: "GitHub rejected the source's token." } }));
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect((await screen.findByRole("alert")).textContent).toBe("GitHub rejected the source's token.");
  });

  it("adds a GitHub source and shows its preview before anything changes", async () => {
    handlers[`GET ${P}/sources`] = [
      () =>
        res(200, {
          sources: [],
          credentials: [
            { ref: "clients", repos: ["example-org/clients"] },
            { ref: "org", repos: ["example-org/*", "!example-org/clients"] },
          ],
        }),
      () => res(200, { sources: [{ ...github, id: "s2", status: "new", syncedAt: null }], credentials: [{ ref: "clients", repos: ["example-org/clients"] }] }),
    ];
    handlers[`POST ${P}/sources`] = () => res(201, { ...github, id: "s2", status: "new" });
    handlers[`POST ${P}/sources/s2/sync`] = () =>
      res(200, report({ dryRun: true, counts: { adopted: 268, added: 41, skipped: 17 }, plan: { add: ["plan.yaml"] } }));
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a source" }));
    expect(
      screen.getByText("You can sync from: example-org/clients; example-org/* except example-org/clients"),
    ).toBeTruthy();
    await userEvent.type(screen.getByPlaceholderText(/owner\/name/), "example-org/clients");
    await userEvent.type(screen.getByPlaceholderText(/clients\/acme/), "client-a");
    await userEvent.click(screen.getByRole("button", { name: "Add and preview" }));
    expect(await screen.findByText("Preview: 41 added · 268 matched existing · 17 skipped. Nothing has changed yet.")).toBeTruthy();
    expect(calls.find((c) => c.key === `POST ${P}/sources`)?.body).toMatchObject({
      type: "github",
      repo: "example-org/clients",
      path: "client-a",
      ref: "main",
      credential_ref: "",
    });
    expect(calls.find((c) => c.key === `POST ${P}/sources/s2/sync`)?.body).toMatchObject({ dry_run: true });
  });

  it("explains when the deployment has no GitHub token", async () => {
    handlers[`GET ${P}/sources`] = () => res(200, { sources: [], credentials: [] });
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a source" }));
    await userEvent.type(screen.getByPlaceholderText(/owner\/name/), "example-org/clients");
    expect(screen.getByRole("alert").textContent).toMatch(/GitHub access/);
    expect((screen.getByRole("button", { name: "Add and preview" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("ProjectSources — a sync running on the server", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  const tick = () => act(async () => void vi.advanceTimersByTime(PROGRESS_POLL_MS));
  // The poll's interval is created by an effect that runs after `watching` turns
  // on, so on a slow runner a single tick can land before the interval exists and
  // nothing polls. Tick until the expected text shows, checking BEFORE each tick
  // so a later poll's response can't skip past the state under test. (A plain
  // loop, not waitFor: waitFor's timeout runs on the same fake clock the ticks
  // jump forward.)
  const tickUntil = async (shown: () => boolean) => {
    for (let i = 0; i < 5 && !shown(); i++) await tick();
    expect(shown()).toBe(true);
  };

  it("follows a sync already running when the page opens, then shows its result", async () => {
    handlers[`GET ${P}/sources`] = () =>
      res(200, { sources: [{ ...github, status: "running", progress: { done: 4, total: 10 } }], credentials: [] });
    handlers[`GET ${P}/sources/s1`] = [
      () => res(200, { ...github, status: "running", progress: { done: 7, total: 10 } }),
      () => res(200, { ...github, status: "ok", progress: null, lastReport: report({ counts: { added: 10 } }) }),
    ];
    render(<ProjectSources projectId="p1" />);
    const progress = await screen.findByTestId("sync-progress");
    expect(progress.textContent).toMatch(/Syncing on the server — 4 of 10 files/);
    expect(progress.textContent).toMatch(/You can leave this page/);
    expect((screen.getByRole("button", { name: "Syncing…" }) as HTMLButtonElement).disabled).toBe(true);
    await tickUntil(() => /7 of 10 files/.test(screen.queryByTestId("sync-progress")?.textContent ?? ""));
    await tickUntil(() => screen.queryByText("Synced: 10 added.") !== null);
    expect(screen.queryByTestId("sync-progress")).toBeNull();
  });

  it("joins a sync that is already running instead of reporting an error", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = () =>
      res(409, { detail: { code: "SOURCE_SYNC_RUNNING", message: "Another sync of this source is running." } });
    handlers[`GET ${P}/sources/s1`] = [
      () => res(200, { ...github, status: "running", progress: { done: 2, total: 5 } }),
      () => res(200, { ...github, status: "ok", progress: null, lastReport: report({ counts: { updated: 5 } }) }),
    ];
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ProjectSources projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("A sync is already running — following it here.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    await tick();
    await waitFor(() => expect(screen.getByTestId("sync-progress").textContent).toMatch(/2 of 5 files/));
    await tick();
    expect(await screen.findByText("Synced: 5 updated.")).toBeTruthy();
  });

  // fetchWithAuth's own timeout error calls itself TimeoutError.
  const timedOut = () => Promise.reject(Object.assign(new Error("took too long"), { name: "TimeoutError" }));
  const now = () => new Date().toISOString();

  it("waits longer than the server's time limit for a sync", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = () => res(200, report());
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ProjectSources projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText(/^Synced:/)).toBeTruthy();
    const sync = mockFetch.mock.calls.find(([url]) => String(url).endsWith("/sync"));
    expect(sync?.[2]).toEqual({ timeoutMs: SYNC_ANSWER_TIMEOUT_MS });
    expect(SYNC_ANSWER_TIMEOUT_MS).toBeGreaterThan((240 + 120) * 1000);
  });

  it("follows a sync whose answer never came instead of calling it a network error", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = timedOut;
    handlers[`GET ${P}/sources/s1`] = [
      () => res(200, { ...github, status: "running", progress: { done: 63, total: 148 } }),
      () => res(200, { ...github, status: "ok", progress: null, lastReport: report({ counts: { added: 148 }, finishedAt: now() }) }),
    ];
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ProjectSources projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("The sync is still running on the server — following it here.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    await tickUntil(() => /63 of 148 files/.test(screen.queryByTestId("sync-progress")?.textContent ?? ""));
    await tickUntil(() => screen.queryByText("Synced: 148 added.") !== null);
  });

  it("continues a followed sync that stopped at the time limit", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = [timedOut, () => res(200, report({ counts: { added: 30, remaining: 0 } }))];
    handlers[`GET ${P}/sources/s1`] = () =>
      res(200, {
        ...github,
        status: "partial",
        progress: null,
        lastReport: report({ status: "partial", counts: { added: 118, remaining: 30 }, finishedAt: now() }),
      });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ProjectSources projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    await tickUntil(() => screen.queryByText("Synced: 30 added.") !== null);
    const syncs = calls.filter((c) => c.key === `POST ${P}/sources/s1/sync`);
    expect(syncs).toHaveLength(2);
    expect(syncs[1].body).toMatchObject({ force: false });
  });

  it("still reports a sync that never reached the server", async () => {
    handlers[`POST ${P}/sources/s1/sync`] = () => Promise.reject(new TypeError("Failed to fetch"));
    handlers[`GET ${P}/sources/s1`] = () =>
      res(200, { ...github, status: "ok", lastReport: report({ finishedAt: "2026-01-01T00:00:00Z" }) });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ProjectSources projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    await tickUntil(() => screen.queryByText("Couldn't sync (network error).") !== null);
    expect(screen.getByRole("alert").textContent).toBe("Couldn't sync (network error).");
  });
});

describe("ProjectSources — letting the AI write", () => {
  const withSource = (extra: Record<string, unknown>) => {
    handlers[`GET ${P}/sources`] = () => res(200, { sources: [{ ...github, ...extra }], credentials: [] });
  };

  it("offers no switch where writing isn't set up", async () => {
    render(<ProjectSources projectId="p1" />);
    await screen.findByTestId("project-source");
    expect(screen.queryByLabelText("Let the AI write here")).toBeNull();
    expect(screen.queryByTestId("source-write-install")).toBeNull();
  });

  it("turns writing on for a source", async () => {
    withSource({ write: { canWrite: true } });
    handlers[`PATCH ${P}/sources/s1`] = () => res(200, { ...github, writable: true, write: { canWrite: true } });
    render(<ProjectSources projectId="p1" />);
    const box = (await screen.findByLabelText("Let the AI write here")) as HTMLInputElement;
    expect(box.checked).toBe(false);
    await userEvent.click(box);
    await waitFor(() => expect(box.checked).toBe(true));
    expect(calls.find((c) => c.key === `PATCH ${P}/sources/s1`)?.body).toEqual({ writable: true });
    expect(screen.getByText(/can commit text files to main/)).toBeTruthy();
  });

  it("says how to install the writer App where it isn't installed yet", async () => {
    const installUrl = "https://github.com/apps/example-writer/installations/new";
    withSource({ write: { canWrite: false, installUrl } });
    render(<ProjectSources projectId="p1" />);
    const note = await screen.findByTestId("source-write-install");
    expect(note.textContent).toMatch(/must be installed on example-org\/clients/);
    expect(within(note).getByRole("link", { name: "Install the writer App on GitHub" }).getAttribute("href")).toBe(installUrl);
    expect(screen.queryByLabelText("Let the AI write here")).toBeNull();
  });

  it("adds a source with writing turned on", async () => {
    handlers[`GET ${P}/sources`] = () =>
      res(200, { sources: [], credentials: [{ ref: "clients", repos: ["example-org/clients"] }], writers: [{ ref: "w", repos: ["example-org/*"] }] });
    handlers[`POST ${P}/sources`] = () => res(201, { ...github, status: "new" });
    handlers[`PATCH ${P}/sources/s1`] = () => res(200, { ...github, writable: true });
    handlers[`POST ${P}/sources/s1/sync`] = () => res(200, report({ dryRun: true }));
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Add a source/ }));
    await userEvent.type(screen.getByPlaceholderText(/owner\/name/), "example-org/clients");
    await userEvent.click(screen.getByLabelText("Let the AI write here"));
    await userEvent.click(screen.getByRole("button", { name: "Add and preview" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${P}/sources/s1/sync`)).toBe(true));
    const keys = calls.map((c) => c.key);
    expect(keys.indexOf(`PATCH ${P}/sources/s1`)).toBeGreaterThan(keys.indexOf(`POST ${P}/sources`));
    expect(calls.find((c) => c.key === `PATCH ${P}/sources/s1`)?.body).toEqual({ writable: true });
  });

  it("offers no write switch on the add form without write access", async () => {
    handlers[`GET ${P}/sources`] = () => res(200, { sources: [], credentials: [{ ref: "clients", repos: ["example-org/clients"] }] });
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Add a source/ }));
    expect(screen.queryByLabelText("Let the AI write here")).toBeNull();
  });

  it("shows why turning it on failed", async () => {
    withSource({ write: { canWrite: true } });
    handlers[`PATCH ${P}/sources/s1`] = () =>
      res(409, { detail: { code: "SOURCE_WRITE_NOT_INSTALLED", message: "The writer GitHub App isn't installed on example-org/clients yet." } });
    render(<ProjectSources projectId="p1" />);
    await userEvent.click(await screen.findByLabelText("Let the AI write here"));
    expect(await screen.findByText("The writer GitHub App isn't installed on example-org/clients yet.")).toBeTruthy();
  });
});
