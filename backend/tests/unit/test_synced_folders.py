"""Which library folders a GitHub source writes into (sources/synced_folders.py)."""

from __future__ import annotations

from unittest.mock import patch

from sources.synced_folders import covering_source, synced_prefixes


def test_covering_source_matches_the_folder_its_subfolders_and_its_parents():
    entry = {"prefix": "client/docs", "projectId": "p1"}
    for name in ("client/docs", "Client/Docs", "client/docs/a/b", "client", " client "):
        assert covering_source([entry], name) is entry, name
    for name in ("", "client/docs2", "clientele", "docs", "other/client/docs"):
        assert covering_source([entry], name) is None, name


def test_synced_prefixes_keeps_github_sources_and_names_their_project():
    sources = [
        {"id": "s1", "type": "github", "folderPrefix": "/docs/", "projectId": "p1", "repo": "example-org/app"},
        {"id": "s2", "type": "folder", "folderId": "f1", "projectId": "p1"},
        {"id": "s3", "type": "github", "folderPrefix": "", "projectId": "p2"},
        {"id": "s4", "folderPrefix": "notes", "projectId": "p1", "repo": "example-org/notes"},
    ]
    with (
        patch("sources.synced_folders.store.list_user_sources", return_value=sources),
        patch("sources.synced_folders.projects_db.get_project", return_value={"name": "AIPLA"}) as get_project,
    ):
        out = synced_prefixes("mark")
    assert [(e["prefix"], e["projectName"], e["sourceId"]) for e in out] == [
        ("docs", "AIPLA", "s1"),
        ("notes", "AIPLA", "s4"),
    ]
    get_project.assert_called_once_with("mark", "p1")
