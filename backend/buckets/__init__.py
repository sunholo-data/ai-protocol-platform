"""Bucket + Folder resource module (RESOURCE-ACCESS 1A.1b).

Exports:
    - bucket_config: CRUD for /buckets/{bucketId}
    - folder_config: CRUD for /buckets/{bucketId}/folders/{folderId}
                     plus compute_effective_access() for write-time inheritance
    - routes: FastAPI router mounted at /api/buckets
"""

from __future__ import annotations
