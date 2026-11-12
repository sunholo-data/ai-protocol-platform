"""GitHub credentials for sources: configuration, policy, and App tokens
(github-sources.md §16).

The App flow runs against httpx's MockTransport with a throwaway RSA key, so the
JWT is really signed and really verified, and the token request's scope (one
repository, Contents: read) is asserted on the wire.
"""

from __future__ import annotations

import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.access_context import AccessContext
from sources import credentials, github_app
from sources.github import GitHubError

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
_PUBLIC = _KEY.public_key()

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
            "installation_id": "77",
            "repos": ["example-org/clients"],
            "users": {"type": "specific", "emails": ["lead@example.com"]},
        },
        "legacy": {
            "token_env": "TEST_PAT",
            "repos": ["other-org/handbook"],
            "users": {"type": "domain", "domain": "example.com"},
        },
    }
}


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(CONFIG))
    # A key pasted into a one-line env var keeps "\n" escapes.
    monkeypatch.setenv("TEST_APP_KEY", _PEM.replace("\n", "\\n"))
    monkeypatch.setenv("TEST_PAT", "pat-value")
    github_app._forget_cached()
    yield
    github_app._forget_cached()


def _ctx(email: str, tags: tuple[str, ...] = (), domain: str = "example.com") -> AccessContext:
    return AccessContext(uid=email.split("@")[0], email=email, domain=domain, group_tags=frozenset(tags))


def test_offered_follows_each_credentials_users_rule():
    assert [c.ref for c in credentials.offered(_ctx("staff@example.com", ("staff",)))] == ["docs", "legacy"]
    assert [c.ref for c in credentials.offered(_ctx("lead@example.com"))] == ["client-files", "legacy"]
    assert credentials.offered(_ctx("someone@elsewhere.com", domain="elsewhere.com")) == []


def test_repos_are_owner_name_globs():
    docs = credentials.get("docs")
    assert docs and docs.covers("example-org/blog") and docs.covers("Example-Org/New-Repo")
    assert not docs.covers("other-org/blog")
    # "!" leaves one out: client files stay with the credential meant for them.
    assert not docs.covers("example-org/clients") and not docs.covers("Example-Org/Clients")
    assert credentials.get("client-files").covers("example-org/clients")


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"token_env": "X", "repos": ["nope"]}, "owner/name"),
        ({"token_env": "X"}, "owner/name"),
        ({"token_env": "X", "repos": ["!o/r"]}, "owner/name"),
        ({"repos": ["o/r"]}, "either a GitHub App"),
        ({"app_id": "1", "token_env": "X", "repos": ["o/r"]}, "either a GitHub App"),
        ({"app_id": "1", "repos": ["o/r"]}, "private_key_env"),
        ({"token_env": "X", "repos": ["o/r"], "users": {"type": "everyone"}}, "access rule"),
    ],
)
def test_bad_credentials_are_refused(row, message):
    with pytest.raises(credentials.CredentialConfigError, match=message):
        credentials._parse(json.dumps({"credentials": {"c": row}}))


def test_a_broken_config_offers_nothing(monkeypatch):
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", '{"credentials": {"c": {"token_env": "X"}}}')
    assert credentials.load() == {}


def test_yaml_file_config(tmp_path, monkeypatch):
    path = tmp_path / "creds.yaml"
    path.write_text("credentials:\n  docs:\n    token_env: TEST_PAT\n    repos: [example-org/*]\n")
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", str(path))
    assert list(credentials.load()) == ["docs"]
    assert credentials.get("docs").users.type == "private"  # nobody, until said otherwise


async def test_a_token_is_only_for_covered_repositories():
    with pytest.raises(GitHubError) as exc:
        await credentials.token(credentials.get("legacy"), "example-org/blog")
    assert exc.value.code == "SOURCE_REPO_NOT_COVERED"
    assert await credentials.token(credentials.get("legacy"), "other-org/handbook") == "pat-value"


def _github(calls: list[httpx.Request], *, installed: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        claims = jwt.decode(request.headers["Authorization"].removeprefix("Bearer "), _PUBLIC, algorithms=["RS256"])
        assert claims["iss"] == "4242" and claims["exp"] - claims["iat"] <= 600
        if request.url.path == "/repos/example-org/blog/installation":
            return httpx.Response(200, json={"id": 55}) if installed else httpx.Response(404)
        if request.url.path in ("/app/installations/55/access_tokens", "/app/installations/77/access_tokens"):
            return httpx.Response(
                201, json={"token": f"ghs_{request.url.path.split('/')[3]}", "expires_at": "2099-01-01T00:00:00Z"}
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_an_app_token_is_minted_for_one_repository_read_only_and_cached():
    calls: list[httpx.Request] = []
    http = _github(calls)
    docs = credentials.get("docs")
    assert await github_app.installation_token(docs, "example-org/blog", http=http) == "ghs_55"
    token_request = calls[-1]
    assert json.loads(token_request.content) == {"repositories": ["blog"], "permissions": {"contents": "read"}}
    assert await github_app.installation_token(docs, "example-org/blog", http=http) == "ghs_55"
    assert len(calls) == 2  # installation lookup + mint; the second call was cached


async def test_a_configured_installation_skips_the_lookup():
    calls: list[httpx.Request] = []
    token = await github_app.installation_token(
        credentials.get("client-files"), "example-org/clients", http=_github(calls)
    )
    assert token == "ghs_77"
    assert [c.url.path for c in calls] == ["/app/installations/77/access_tokens"]


async def test_an_app_not_installed_says_what_to_do():
    with pytest.raises(GitHubError) as exc:
        await github_app.installation_token(
            credentials.get("docs"), "example-org/blog", http=_github([], installed=False)
        )
    assert exc.value.code == "SOURCE_NOT_VISIBLE"
    assert "install it" in exc.value.message


async def test_a_write_token_names_one_repository_and_contents_write():
    calls: list[httpx.Request] = []
    writer = credentials.Credential(
        ref="docs-write", repos=("example-org/*",), app_id="4242", private_key_env="TEST_APP_KEY", access="write"
    )
    token = await github_app.installation_token(
        writer, "example-org/blog", permissions={"contents": "write"}, http=_github(calls)
    )
    assert token == "ghs_55"
    assert json.loads(calls[-1].content) == {"repositories": ["blog"], "permissions": {"contents": "write"}}
    # A read token for the same repository is a different token, never the write one reused.
    await github_app.installation_token(writer, "example-org/blog", http=_github(calls))
    assert json.loads(calls[-1].content)["permissions"] == {"contents": "read"}


async def test_installed_on_and_the_install_link():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/example-org/blog/installation":
            return httpx.Response(404)
        if request.url.path == "/app":
            return httpx.Response(200, json={"slug": "example-writer"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    docs = credentials.get("docs")
    assert await github_app.installed_on(docs, "example-org/blog", http=http) is False
    assert await github_app.install_url(docs, http=http) == "https://github.com/apps/example-writer/installations/new"
