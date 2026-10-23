"""Generic seeding keeps domain grants distinct from fallback grants."""

from scripts.seed_tool_permissions import CREATED_BY, DEFAULT_TOOLS, build_docs


def test_domain_grant_and_wildcard_are_separate_documents():
    docs = dict(build_docs(domain="customer.example", email=None, tools=None, wildcard=True))
    assert docs["customer.example"] == {
        "type": "domain",
        "tools": ["*"],
        "denied": [],
        "created_by": CREATED_BY,
    }
    assert docs["*"]["tools"] == DEFAULT_TOOLS


def test_explicit_tools_never_expand_to_wildcard():
    docs = dict(
        build_docs(domain="customer.example", email="reader@customer.example", tools=["google_search"], wildcard=False)
    )
    assert set(docs) == {"customer.example", "reader@customer.example"}
    assert all(doc["tools"] == ["google_search"] for doc in docs.values())
