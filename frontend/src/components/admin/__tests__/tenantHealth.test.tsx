// Guards the tenant-admin overview health bits (v6.9.x richer-UI quick-wins):
// the bucket-reachability dot + the at-a-glance config-health badge, both driven
// by the /api/admin/tenants/{domain}/validate response.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TenantValidation } from "../tenantAdmin";
import { bucketLevel, HealthBadge, healthSummary, ReachDot } from "../tenantHealth";

function validation(checks: TenantValidation["checks"], ok = true): TenantValidation {
  return { domain: "acme.com", ok, checks };
}

describe("bucketLevel", () => {
  it("returns null until the probe resolves", () => {
    expect(bucketLevel(undefined)).toBeNull();
    expect(bucketLevel({ kind: "loading" })).toBeNull();
    expect(bucketLevel({ kind: "error" })).toBeNull();
  });

  it("extracts the documents_bucket check level", () => {
    const state = {
      kind: "ready" as const,
      validation: validation([{ field: "documents_bucket", level: "ok", message: "" }]),
    };
    expect(bucketLevel(state)).toBe("ok");
  });

  it("is null when there is no bucket check", () => {
    const state = {
      kind: "ready" as const,
      validation: validation([{ field: "default_skill", level: "ok", message: "" }]),
    };
    expect(bucketLevel(state)).toBeNull();
  });
});

describe("healthSummary", () => {
  it("counts warnings and errors, ignoring ok/skipped", () => {
    const v = validation([
      { field: "a", level: "ok", message: "" },
      { field: "b", level: "warning", message: "" },
      { field: "c", level: "warning", message: "" },
      { field: "d", level: "error", message: "" },
      { field: "e", level: "skipped", message: "" },
    ]);
    expect(healthSummary(v)).toEqual({ warnings: 2, errors: 1 });
  });
});

describe("HealthBadge", () => {
  it("shows 'checking…' while loading", () => {
    render(<HealthBadge state={{ kind: "loading" }} />);
    expect(screen.getByText(/checking/i)).toBeTruthy();
  });

  it("shows healthy when all checks pass", () => {
    render(
      <HealthBadge
        state={{
          kind: "ready",
          validation: validation([{ field: "documents_bucket", level: "ok", message: "" }]),
        }}
      />,
    );
    expect(screen.getByText(/healthy/i)).toBeTruthy();
  });

  it("shows the warning count (e.g. unreachable bucket)", () => {
    render(
      <HealthBadge
        state={{
          kind: "ready",
          validation: validation([{ field: "documents_bucket", level: "warning", message: "" }]),
        }}
      />,
    );
    expect(screen.getByText(/1 warning/i)).toBeTruthy();
  });

  it("shows 'needs fix' when a check errors (or ok=false)", () => {
    render(
      <HealthBadge
        state={{
          kind: "ready",
          validation: validation([{ field: "default_skill", level: "error", message: "" }], false),
        }}
      />,
    );
    expect(screen.getByText(/needs fix/i)).toBeTruthy();
  });
});

describe("ReachDot", () => {
  it("labels reachability from the bucket check (accessible title)", () => {
    const { rerender } = render(
      <ReachDot
        state={{
          kind: "ready",
          validation: validation([{ field: "documents_bucket", level: "ok", message: "" }]),
        }}
      />,
    );
    expect(screen.getByLabelText(/reachable by the service account/i)).toBeTruthy();

    rerender(
      <ReachDot
        state={{
          kind: "ready",
          validation: validation([{ field: "documents_bucket", level: "warning", message: "" }]),
        }}
      />,
    );
    expect(screen.getByLabelText(/not reachable/i)).toBeTruthy();
  });
});
