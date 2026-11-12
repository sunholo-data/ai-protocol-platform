"""GitHub App tokens for MCP servers (``auth: github_app``).

What matters: the token a caller gets names exactly the repositories that caller
may read under the deployment's GitHub credentials, its permissions are
read-only, and it is stable across calls (ADK keys its MCP session pool on the
headers). GitHub is httpx's MockTransport; the App JWT is really signed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.access_context import AccessContext
from sources import github_app
from tools.mcp import github_app_auth
from tools.mcp.github_app_auth import GitHubMcpAccessError
from tools.mcp.registry import _build_header_provider

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()

CONFIG = {
    "credentials": {
        "docs": {
            "app_id": "4242",
            "private_key_env": "TEST_APP_KEY",
            "repos": ["example-org/*", "!example-org/clients"],
            "users": {"type": "tagged", "tags": ["staff"]},
        },
        "client-files": {
            "app_id": "4242",
            "private_key_env": "TEST_APP_KEY",
            "installation_id": "55",
            "repos": ["example-org/clients"],
            "users": {"type": "specific", "emails": ["lead@example.com"]},
        },
        "elsewhere": {
            "app_id": "4242",
            "private_key_env": "TEST_APP_KEY",
            "repos": ["other-org/handbook"],
            "users": {"type": "specific", "emails": ["wanderer@example.com"]},
        },
        "legacy": {
            "token_env": "TEST_PAT",
            "repos": ["example-org/blog"],
            "users": {"type": "domain", "domain": "example.com"},
        },
    }
}

INSTALLED = ["example-org/blog", "example-org/clients", "example-org/handbook"]


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(CONFIG))
    monkeypatch.setenv("TEST_APP_KEY", _PEM.replace("\n", "\\n"))
    monkeypatch.setenv("TEST_PAT", "pat-value")
    github_app._forget_cached()
    yield
    github_app._forget_cached()


def _ctx(email: str, tags: tuple[str, ...] = ()) -> AccessContext:
    return AccessContext(uid="", email=email, domain=email.split("@")[1], group_tags=frozenset(tags))


class _GitHub:
    """Records requests; answers the installation lookups, the repository list,
    and token mints (each mint gets a new token, so caching is observable)."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.mints: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/orgs/example-org/installation":
            return httpx.Response(200, json={"id": 55})
        if path == "/orgs/other-org/installation":
            return httpx.Response(200, json={"id": 66})
        if path.endswith("/access_tokens"):
            body = json.loads(request.content)
            self.mints.append({"installation": path.split("/")[3], **body})
            return httpx.Response(201, json={"token": f"ghs_{len(self.mints)}", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/installation/repositories":
            return httpx.Response(
                200, json={"total_count": len(INSTALLED), "repositories": [{"full_name": r} for r in INSTALLED]}
            )
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def test_the_token_names_only_the_repositories_the_caller_may_read():
    gh = _GitHub()
    token = await github_app_auth.token_for(_ctx("staffer@example.com", ("staff",)), {}, http=gh.client())

    listing, scoped = gh.mints
    # The repository list is read with a metadata-only token...
    assert listing == {"installation": "55", "permissions": {"metadata": "read"}}
    # ...and the caller's token leaves out the excluded repository.
    assert scoped == {"installation": "55", "repositories": ["blog", "handbook"], "permissions": {"contents": "read"}}
    assert token == "ghs_2"


async def test_credentials_that_admit_the_caller_add_up():
    gh = _GitHub()
    await github_app_auth.token_for(_ctx("lead@example.com", ("staff",)), {}, http=gh.client())
    assert gh.mints[-1]["repositories"] == ["blog", "clients", "handbook"]


async def test_a_row_can_narrow_to_named_credentials():
    gh = _GitHub()
    config = {"credentials": ["client-files"]}
    await github_app_auth.token_for(_ctx("lead@example.com", ("staff",)), config, http=gh.client())
    assert gh.mints[-1]["repositories"] == ["clients"]


async def test_the_token_is_stable_across_calls():
    gh = _GitHub()
    ctx = _ctx("staffer@example.com", ("staff",))
    first = await github_app_auth.token_for(ctx, {}, http=gh.client())
    for _ in range(5):
        assert await github_app_auth.token_for(ctx, {}, http=gh.client()) == first
    assert len(gh.mints) == 2, "re-minted: ADK's MCP session pool would churn"


async def test_a_caller_without_access_gets_no_token():
    gh = _GitHub()
    with pytest.raises(GitHubMcpAccessError, match="don't have access"):
        await github_app_auth.token_for(_ctx("visitor@example.com"), {}, http=gh.client())
    assert gh.calls == []


def test_a_token_credential_is_not_used():
    # "legacy" admits everyone at example.com but is a personal token, not an App.
    assert [c.ref for c in github_app_auth.usable_credentials(_ctx("visitor@example.com"), {})] == []


@pytest.mark.parametrize(
    "perms",
    [{"contents": "write"}, {"administration": "admin"}, {"Contents; drop": "read"}, ["contents"]],
)
async def test_anything_but_read_is_refused(perms):
    with pytest.raises(GitHubMcpAccessError):
        await github_app_auth.token_for(_ctx("staffer@example.com", ("staff",)), {"permissions": perms})


async def test_extra_read_permissions_can_be_asked_for():
    gh = _GitHub()
    config = {"permissions": {"contents": "read", "pull_requests": "read"}}
    await github_app_auth.token_for(_ctx("staffer@example.com", ("staff",)), config, http=gh.client())
    assert gh.mints[-1]["permissions"] == {"contents": "read", "pull_requests": "read"}


async def test_access_on_two_accounts_needs_an_installation_choice():
    gh = _GitHub()
    both = _ctx("wanderer@example.com", ("staff",))
    with pytest.raises(GitHubMcpAccessError, match="installation_id"):
        await github_app_auth.token_for(both, {}, http=gh.client())
    await github_app_auth.token_for(both, {"installation_id": "55"}, http=gh.client())
    assert gh.mints[-1]["installation"] == "55"


async def test_the_registry_wires_the_provider_with_the_callers_tags(monkeypatch):
    seen: list[AccessContext] = []

    async def fake_token_for(ctx, config, *, http=None):
        seen.append(ctx)
        return "ghs_x"

    monkeypatch.setattr(github_app_auth, "token_for", fake_token_for)
    provider = _build_header_provider(
        "github", {"auth": "github_app"}, "staffer@example.com", "example.com", frozenset({"staff"})
    )
    assert await provider(object()) == {"Authorization": "Bearer ghs_x"}
    assert seen[0].email == "staffer@example.com" and seen[0].group_tags == frozenset({"staff"})
