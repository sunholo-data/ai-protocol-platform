"""Internal task routes — endpoints only Cloud Tasks may call.

The backend service is public (it serves the frontend), so Cloud Run IAM
cannot gate these; each route verifies the task's OIDC token in-app. See
``internal_tasks.auth`` for the gate and
docs/design/v6.23.0/compaction-second-pass.md for the trust chain.
"""
