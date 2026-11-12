"""Use the maintained CA bundle across Python HTTP clients and gRPC.

gRPC bundles its own roots and OpenSSL may also load a system CA directory.
Updating Certifi alone does not remove distrusted roots from those stores.
Explicit operator overrides remain authoritative.
"""

import os
from pathlib import Path

import certifi


def configure_tls_roots() -> None:
    bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    # OpenSSL loads both a CA file and a hashed CA directory. Certifi's package
    # directory contains no system-root symlinks, preventing a stale fallback.
    os.environ.setdefault("SSL_CERT_DIR", str(Path(bundle).parent))
    selected = os.environ["SSL_CERT_FILE"]
    os.environ.setdefault("REQUESTS_CA_BUNDLE", selected)
    os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", selected)
