import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

// Capture the auth listener so a test can sign one user out and another in.
let authCallback: ((user: { uid: string } | null) => void) | null = null;
vi.mock("@/lib/firebase", () => ({
  subscribeToAuthState: (cb: (user: { uid: string } | null) => void) => {
    authCallback = cb;
    return () => {
      authCallback = null;
    };
  },
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { notifyProjectsChanged } from "@/lib/projectEvents";
import {
  __resetProjectsStore,
  addDocumentsToProject,
  createProject,
  createProjectWithFreeName,
  removeDocumentFromProject,
  useProject,
  useProjects,
  useProjectSessions,
} from "@/hooks/useProjects";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

const iberia = { id: "p1", name: "Iberia portfolio review", docCount: 3, chatCount: 2, lastOpenedAt: "2026-10-19T10:00:00Z" };
const dispute = { id: "p2", name: "Settlement dispute", docCount: 1, chatCount: 0, lastOpenedAt: "2026-10-20T09:00:00Z" };

beforeEach(() => {
  __resetProjectsStore();
  mockFetch.mockReset();
});
afterEach(() => __resetProjectsStore());

describe("useProjects — the shared list", () => {
  it("loads once, most recently opened first", async () => {
    mockFetch.mockResolvedValue(res(200, { projects: [iberia, dispute] }));
    const a = renderHook(() => useProjects());
    const b = renderHook(() => useProjects());
    await waitFor(() => expect(a.result.current.status).toBe("ready"));
    expect(a.result.current.projects.map((p) => p.name)).toEqual(["Settlement dispute", "Iberia portfolio review"]);
    expect(b.result.current.projects).toHaveLength(2);
    // Two consumers (e.g. the sidebar and a document menu) share one fetch.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/projects");
  });

  it("a first-load failure is an error the UI can retry, never an empty list", async () => {
    mockFetch.mockResolvedValueOnce(res(500));
    const { result } = renderHook(() => useProjects());
    await waitFor(() => expect(result.current.status).toBe("error"));
    mockFetch.mockResolvedValueOnce(res(200, { projects: [iberia] }));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.projects[0].name).toBe("Iberia portfolio review");
  });

  it("refetches when projects change, keeping the list on screen meanwhile", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { projects: [iberia] }));
    const { result } = renderHook(() => useProjects());
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    mockFetch.mockResolvedValueOnce(res(200, { projects: [iberia, dispute] }));
    act(() => notifyProjectsChanged());
    expect(result.current.status).toBe("ready");
    await waitFor(() => expect(result.current.projects).toHaveLength(2));
  });
});

describe("mutations", () => {
  it("create returns the project and tells every list", async () => {
    const listener = vi.fn();
    window.addEventListener("aitana:projects-changed", listener);
    mockFetch.mockResolvedValueOnce(res(201, { ...iberia, instructions: "", defaultSkillId: null }));
    const r = await createProject({ name: "Iberia portfolio review" });
    expect(r.ok && r.project.id).toBe("p1");
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({ name: "Iberia portfolio review" });
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener("aitana:projects-changed", listener);
  });

  it("a duplicate name surfaces the server's own words and code", async () => {
    mockFetch.mockResolvedValueOnce(
      res(409, { detail: { code: "PROJECT_EXISTS", message: "You already have a project called 'Iberia'." } }),
    );
    const r = await createProject({ name: "Iberia" });
    expect(r).toEqual({ ok: false, code: "PROJECT_EXISTS", message: "You already have a project called 'Iberia'." });
  });

  it("a pre-filled name asks the server for a free one (uniqueName) in a single call", async () => {
    mockFetch.mockResolvedValueOnce(res(201, { id: "p9", name: "PPAs (2)", documents: { added: ["d1"], already: [], failed: [] } }));
    const r = await createProjectWithFreeName({ name: " PPAs ", fromFolderId: "f1" });
    expect(r.ok && r.project.name).toBe("PPAs (2)");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({ name: "PPAs", fromFolderId: "f1", uniqueName: true });
  });

  it("a 422 shows the server's message verbatim", async () => {
    mockFetch.mockResolvedValueOnce(
      res(422, { detail: { code: "PROJECT_NAME_INVALID", message: "A project name can be at most 80 characters." } }),
    );
    const r = await createProject({ name: "x" });
    expect(r).toEqual({ ok: false, code: "PROJECT_NAME_INVALID", message: "A project name can be at most 80 characters." });
  });

  it("adding documents reports added / already / failed", async () => {
    mockFetch.mockResolvedValueOnce(
      res(200, { added: ["d1"], already: ["d2"], failed: [{ id: "d3", filename: "x.pdf", reason: "not yours" }] }),
    );
    const r = await addDocumentsToProject("p1", ["d1", "d2", "d3"]);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/projects/p1/documents");
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({ doc_ids: ["d1", "d2", "d3"] });
    expect(r).toEqual({ ok: true, added: ["d1"], already: ["d2"], failed: [{ id: "d3", filename: "x.pdf", reason: "not yours" }] });
  });

  it("a network failure is a message, not a throw", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const r = await removeDocumentFromProject("p1", "d1");
    expect(r).toEqual({ ok: false, message: "Couldn't remove it from the project (network error)." });
  });
});

describe("useProject — one home", () => {
  it("a 404 is 'not found' (deleted), distinct from an error", async () => {
    mockFetch.mockResolvedValueOnce(res(404, { detail: "Not found" }));
    const { result } = renderHook(() => useProject("gone"));
    await waitFor(() => expect(result.current.status).toBe("not_found"));
  });

  it("reads the project, tolerating snake_case keys", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { id: "p1", name: "Iberia", doc_count: 4, default_skill_id: "s1", instructions: "Be brief" }));
    const { result } = renderHook(() => useProject("p1"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.project).toMatchObject({ name: "Iberia", docCount: 4, defaultSkillId: "s1", instructions: "Be brief" });
  });
});

describe("useProjectSessions", () => {
  it("lists explicit and derived chats with the linking document", async () => {
    mockFetch.mockResolvedValueOnce(
      res(200, {
        sessions: [
          { session_id: "s1", title: "EDP review", membership: "derived", linked_document: { id: "d1", filename: "EDP.pdf" } },
        ],
        next_cursor: "s1",
      }),
    );
    const { result } = renderHook(() => useProjectSessions("p1"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/projects/p1/sessions?page_size=30");
    expect(result.current.sessions[0].linkedDocument).toEqual({ id: "d1", filename: "EDP.pdf" });
    expect(result.current.hasMore).toBe(true);
  });
});

describe("useProjects — a different user signs in", () => {
  it("drops the previous user's projects and loads the new user's", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { projects: [iberia] }));
    const { result } = renderHook(() => useProjects());
    act(() => authCallback?.({ uid: "user-a" }));
    await waitFor(() => expect(result.current.projects.map((p) => p.name)).toEqual(["Iberia portfolio review"]));

    mockFetch.mockResolvedValueOnce(res(200, { projects: [dispute] }));
    act(() => authCallback?.(null));
    expect(result.current.projects).toEqual([]);
    act(() => authCallback?.({ uid: "user-b" }));
    await waitFor(() => expect(result.current.projects.map((p) => p.name)).toEqual(["Settlement dispute"]));
  });
});
