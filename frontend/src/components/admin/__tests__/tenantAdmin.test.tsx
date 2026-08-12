/**
 * Tenant-admin component tests (v6.9.0 M4).
 *
 * Focus on the ERROR + EMPTY paths (not just happy) per the never-silent rule:
 *   - onboard 422 unknown_skill_ref renders the exact bad slugs
 *   - onboard 409 renders "already exists"
 *   - onboard 201 renders each validation step's verdict (incl. bucket WARNING)
 *   - editor save 422 renders bad refs
 *   - editor "Validate" renders a bucket IAM-unreachable warning
 *   - SkillMultiSelect empty catalog renders a visible notice (not a blank box)
 *   - pure helpers (parseUnknownSkillRefs / csvToList)
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuthMock = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuthMock(...args),
}));

import {
  csvToList,
  parseUnknownSkillRefs,
  SkillMultiSelect,
  ValidationVerdicts,
  type SkillOption,
  type TenantValidation,
} from "../tenantAdmin";
import { TenantEditor } from "../TenantEditor";
import { TenantOnboardWizard } from "../TenantOnboardWizard";

const SKILLS: SkillOption[] = [
  { slug: "one-assistant", displayName: "ONE Assistant" },
  { slug: "one-ppa-expert", displayName: "PPA Expert" },
];

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  fetchWithAuthMock.mockReset();
});

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe("helpers", () => {
  it("parseUnknownSkillRefs pulls the unknown list from a 422 detail", () => {
    expect(parseUnknownSkillRefs({ unknown: ["a", "b"] })).toEqual(["a", "b"]);
    expect(parseUnknownSkillRefs("nope")).toEqual([]);
    expect(parseUnknownSkillRefs({})).toEqual([]);
  });

  it("csvToList collapses empty to null", () => {
    expect(csvToList(" a, b ,")).toEqual(["a", "b"]);
    expect(csvToList("   ")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// SkillMultiSelect — empty + orphan
// ---------------------------------------------------------------------------

describe("SkillMultiSelect", () => {
  it("renders a visible notice when the catalog is empty (never a blank box)", () => {
    render(<SkillMultiSelect options={[]} selected={[]} onChange={() => {}} />);
    expect(screen.getByText(/no skills available/i)).toBeInTheDocument();
  });

  it("shows a selected slug that is not in the catalog (never silently dropped)", () => {
    render(
      <SkillMultiSelect options={SKILLS} selected={["ghost-skill"]} onChange={() => {}} />,
    );
    expect(screen.getByText(/ghost-skill \(not in catalog\)/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ValidationVerdicts
// ---------------------------------------------------------------------------

describe("ValidationVerdicts", () => {
  it("renders every check including a bucket WARNING", () => {
    const v: TenantValidation = {
      domain: "acme.com",
      ok: true,
      checks: [
        { field: "enabled_skills", level: "ok", message: "resolved" },
        {
          field: "documents_bucket",
          level: "warning",
          message: "exists but the service account cannot read it",
        },
      ],
    };
    render(<ValidationVerdicts validation={v} />);
    expect(screen.getByText(/cannot read it/i)).toBeInTheDocument();
    expect(screen.getByText(/validation passed/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// TenantOnboardWizard — error + success paths
// ---------------------------------------------------------------------------

describe("TenantOnboardWizard", () => {
  it("rejects a domain without a dot before calling the API", () => {
    render(
      <TenantOnboardWizard availableSkills={SKILLS} onCreated={() => {}} onCancel={() => {}} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/acme-corp\.com/i), {
      target: { value: "notadomain" },
    });
    fireEvent.click(screen.getByRole("button", { name: /onboard tenant/i }));
    expect(screen.getByText(/valid email domain/i)).toBeInTheDocument();
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it("renders the exact unknown slugs on a 422", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: { error: "unknown_skill_ref", unknown: ["ghost-skill"] } }),
    );
    render(
      <TenantOnboardWizard availableSkills={SKILLS} onCreated={() => {}} onCancel={() => {}} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/acme-corp\.com/i), {
      target: { value: "acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /onboard tenant/i }));
    await waitFor(() =>
      expect(screen.getByText(/unknown skill slug\(s\): ghost-skill/i)).toBeInTheDocument(),
    );
  });

  it("renders an already-exists notice on a 409", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(jsonResponse(409, { detail: "exists" }));
    render(
      <TenantOnboardWizard availableSkills={SKILLS} onCreated={() => {}} onCancel={() => {}} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/acme-corp\.com/i), {
      target: { value: "acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /onboard tenant/i }));
    await waitFor(() => expect(screen.getByText(/already exists/i)).toBeInTheDocument());
  });

  it("renders per-step verdicts on a 201 (bucket warning surfaced)", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(
      jsonResponse(201, {
        domain: "acme.com",
        config: { domain: "acme.com" },
        validation: {
          domain: "acme.com",
          ok: true,
          checks: [
            { field: "enabled_skills", level: "ok", message: "resolved" },
            {
              field: "documents_bucket",
              level: "warning",
              message: "does not exist or is not visible",
            },
          ],
        },
      }),
    );
    const onCreated = vi.fn();
    render(
      <TenantOnboardWizard availableSkills={SKILLS} onCreated={onCreated} onCancel={() => {}} />,
    );
    fireEvent.change(screen.getByPlaceholderText(/acme-corp\.com/i), {
      target: { value: "acme.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /onboard tenant/i }));
    await waitFor(() => expect(screen.getByText(/onboarded acme\.com/i)).toBeInTheDocument());
    expect(screen.getByText(/not visible/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /done/i }));
    expect(onCreated).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// TenantEditor — error + validate paths
// ---------------------------------------------------------------------------

describe("TenantEditor", () => {
  const tenant = {
    domain: "acme.com",
    display_name: "Acme",
    documents_bucket: "acme-docs",
    enabled_skills: ["one-assistant"],
    default_skill: "one-assistant",
  };

  it("renders bad refs when save returns 422", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: { unknown: ["one-assistant"] } }),
    );
    render(
      <TenantEditor
        tenant={tenant}
        availableSkills={SKILLS}
        onSaved={() => {}}
        onDeleted={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getByText(/unknown skill slug\(s\): one-assistant/i)).toBeInTheDocument(),
    );
  });

  it("renders a bucket IAM-unreachable warning from Validate", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(
      jsonResponse(200, {
        domain: "acme.com",
        ok: true,
        checks: [
          {
            field: "documents_bucket",
            level: "warning",
            message: "the service account cannot read it (grant roles/storage.objectViewer)",
          },
        ],
      }),
    );
    render(
      <TenantEditor
        tenant={tenant}
        availableSkills={SKILLS}
        onSaved={() => {}}
        onDeleted={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /validate/i }));
    await waitFor(() =>
      expect(screen.getByText(/objectViewer/i)).toBeInTheDocument(),
    );
  });

  it("surfaces a forbidden save (403) as a visible notice", async () => {
    fetchWithAuthMock.mockResolvedValueOnce(jsonResponse(403, {}));
    render(
      <TenantEditor
        tenant={tenant}
        availableSkills={SKILLS}
        onSaved={() => {}}
        onDeleted={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.getByText(/aitana-admin group/i)).toBeInTheDocument());
  });

  it("auto-validates after a successful save so a newly-set bucket is checked", async () => {
    fetchWithAuthMock
      .mockResolvedValueOnce(jsonResponse(200, { domain: "acme.com" })) // PUT
      .mockResolvedValueOnce(
        jsonResponse(200, {
          domain: "acme.com",
          ok: true,
          checks: [
            { field: "documents_bucket", level: "ok", message: "reachable by the service account" },
          ],
        }),
      ); // auto validate
    const onSaved = vi.fn();
    render(
      <TenantEditor
        tenant={tenant}
        availableSkills={SKILLS}
        onSaved={onSaved}
        onDeleted={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.getByText(/re-checked below/i)).toBeInTheDocument());
    expect(onSaved).toHaveBeenCalled();
    // the bucket verdict rendered from the auto-validate
    expect(screen.getByText("documents_bucket")).toBeInTheDocument();
    const urls = fetchWithAuthMock.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/api/admin/clients/acme.com"))).toBe(true);
    expect(urls.some((u) => u.includes("/acme.com/validate"))).toBe(true);
  });
});
