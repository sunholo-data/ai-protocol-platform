import { describe, expect, it } from "vitest";
import { parseFolderProjects } from "../useFolderProjects";

describe("parseFolderProjects", () => {
  it("keeps well-formed links and drops the rest", () => {
    const body = {
      folders: {
        f1: [
          { projectId: "p1", projectName: "AIPLA", documents: 4, via: "github" },
          { projectId: "", projectName: "nameless" },
          { projectId: "p2", via: "weird" },
        ],
        f2: [],
        f3: "nope",
      },
    };
    expect(parseFolderProjects(body)).toEqual({
      f1: [
        { projectId: "p1", projectName: "AIPLA", documents: 4, via: "github" },
        { projectId: "p2", projectName: "Untitled project", documents: 0, via: "documents" },
      ],
    });
  });

  it("is empty for a missing or odd body", () => {
    expect(parseFolderProjects(null)).toEqual({});
    expect(parseFolderProjects({ folders: [] })).toEqual({});
  });
});
