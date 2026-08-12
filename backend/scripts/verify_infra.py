#!/usr/bin/env python3
"""Verify GCP infrastructure readiness for Aitana Platform v6.

Checks environment variables, GCP API enablement, and service account
permissions. Run with --dry-run to list checks without making API calls.

Usage:
    uv run python scripts/verify_infra.py           # Full check (requires GCP credentials)
    uv run python scripts/verify_infra.py --dry-run  # List checks only
"""

from __future__ import annotations

import argparse
import os
import sys

# Required env vars for production
_REQUIRED_ENV = {
    "GOOGLE_CLOUD_PROJECT": "GCP project ID",
    "GOOGLE_CLOUD_LOCATION": "GCP region (e.g., europe-west1)",
}

_OPTIONAL_ENV = {
    "AGENT_ENGINE_ID": "Vertex AI Agent Engine resource ID (sessions + memory)",
    "ADK_ARTIFACT_BUCKET": "GCS bucket for ADK artifacts",
    "LOGS_BUCKET_NAME": "GCS bucket for prompt-response logging",
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "Enable Agent Engine tracing",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "Telemetry capture mode (true/NO_CONTENT/false)",
    "ALLOW_ORIGINS": "CORS allowed origins",
}

# GCP APIs that must be enabled
_REQUIRED_APIS = [
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "monitoring.googleapis.com",
]

# IAM roles for platform service account
_REQUIRED_ROLES = [
    "roles/datastore.user",
    "roles/storage.objectAdmin",
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
    "roles/monitoring.metricWriter",
]


def check_env_vars() -> list[str]:
    """Check required and optional environment variables."""
    issues = []
    print("\n=== Environment Variables ===")
    for var, desc in _REQUIRED_ENV.items():
        val = os.environ.get(var)
        if val:
            print(f"  OK   {var}={val}")
        else:
            print(f"  MISS {var} — {desc}")
            issues.append(f"Missing required env var: {var}")

    print()
    for var, desc in _OPTIONAL_ENV.items():
        val = os.environ.get(var)
        if val:
            print(f"  OK   {var}={val}")
        else:
            print(f"  ----  {var} — {desc} (optional)")
    return issues


def check_apis(project: str) -> list[str]:
    """Check GCP API enablement (requires credentials)."""
    issues = []
    print("\n=== GCP API Enablement ===")
    try:
        from google.cloud import service_usage_v1

        client = service_usage_v1.ServiceUsageClient()
        enabled = set()
        for service in client.list_services(request={"parent": f"projects/{project}", "filter": "state:ENABLED"}):
            enabled.add(service.config.name)

        for api in _REQUIRED_APIS:
            if api in enabled:
                print(f"  OK   {api}")
            else:
                print(f"  MISS {api}")
                issues.append(f"API not enabled: {api}")
    except ImportError:
        print("  SKIP google-cloud-service-usage not installed")
    except Exception as e:
        print(f"  ERR  {e}")
        issues.append(f"API check failed: {e}")
    return issues


def dry_run() -> None:
    """List all checks without executing them."""
    print("=== DRY RUN — Checks that would be performed ===\n")

    print("Environment Variables (required):")
    for var, desc in _REQUIRED_ENV.items():
        print(f"  - {var}: {desc}")

    print("\nEnvironment Variables (optional):")
    for var, desc in _OPTIONAL_ENV.items():
        print(f"  - {var}: {desc}")

    print("\nGCP APIs (must be enabled):")
    for api in _REQUIRED_APIS:
        print(f"  - {api}")

    print("\nIAM Roles (for platform SA):")
    for role in _REQUIRED_ROLES:
        print(f"  - {role}")

    print("\nFirestore Collections:")
    print("  - skills/ (with composite indexes)")
    print("  - tool_permissions/")

    print("\nGCS Prefixes:")
    print("  - gs://{ADK_ARTIFACT_BUCKET}/ (artifacts)")
    print("  - gs://{LOGS_BUCKET_NAME}/completions/ (telemetry)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify GCP infrastructure for Aitana v6")
    parser.add_argument("--dry-run", action="store_true", help="List checks without executing")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    issues: list[str] = []
    issues.extend(check_env_vars())

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        issues.extend(check_apis(project))
    else:
        print("\n=== GCP API Enablement ===")
        print("  SKIP GOOGLE_CLOUD_PROJECT not set")

    print(f"\n=== Summary: {len(issues)} issue(s) ===")
    for issue in issues:
        print(f"  - {issue}")

    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
