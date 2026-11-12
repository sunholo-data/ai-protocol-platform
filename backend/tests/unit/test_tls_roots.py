"""Exercise real OpenSSL trust selection, including its lazy CA-directory path."""

import hashlib
import os
import ssl
from pathlib import Path

import certifi

from config.tls import configure_tls_roots

VARIABLES = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
)
REMOVED_ROOTS = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",
    "125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f",
    "bc4d809b15189d78db3e1d8cf4f9726a795da1643ca5f1358e1ddb0edc0d7eb3",
}


def test_default_http_and_grpc_trust_excludes_all_three_removed_roots(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    configure_tls_roots()
    paths = ssl.get_default_verify_paths()
    assert paths.cafile == certifi.where()
    assert Path(paths.capath) == Path(certifi.where()).parent
    # OpenSSL lazily loads hashed files from capath; a clean cafile alone is
    # insufficient if the old system directory remains available as fallback.
    assert not list(Path(paths.capath).glob("????????.[0-9]*"))
    roots = ssl.create_default_context().get_ca_certs(binary_form=True)
    assert len(roots) > 100
    assert not REMOVED_ROOTS.intersection(hashlib.sha256(root).hexdigest() for root in roots)
    for variable in ("REQUESTS_CA_BUNDLE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"):
        assert os.environ[variable] == certifi.where()


def test_explicit_operator_ca_overrides_are_preserved(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.setenv(variable, f"/operator/{variable}")
    configure_tls_roots()
    assert all(os.environ[v] == f"/operator/{v}" for v in VARIABLES)


def test_explicit_ca_file_is_shared_with_clients_unless_independently_overridden(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", "/operator/roots.pem")
    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", "/operator/grpc.pem")
    configure_tls_roots()
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/operator/roots.pem"
    assert os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == "/operator/grpc.pem"
