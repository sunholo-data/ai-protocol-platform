"""Which projects each library folder is in (projects/folder_links.py)."""

from __future__ import annotations

from projects.folder_links import build_links

FOLDERS = [
    {"id": "f-docs", "name": "docs"},
    {"id": "f-design", "name": "docs/design"},
    {"id": "f-notes", "name": "notes"},
    {"id": "f-notes-old", "name": "notes/old"},
    {"id": "f-loose", "name": "loose"},
    {"id": "f-docsx", "name": "docs-x"},
]
PROJECTS = [{"id": "p-a", "name": "AIPLA"}, {"id": "p-b", "name": "beta"}, {"id": "p-c", "name": "Clients"}]


def test_documents_tagged_by_hand_link_their_folder_with_counts():
    links = build_links(FOLDERS, PROJECTS, {"p-b": {"f-loose": 3, "f-notes": 1}}, [])
    assert links["f-loose"] == [{"projectId": "p-b", "projectName": "beta", "documents": 3, "via": "documents"}]
    assert links["f-notes"][0]["documents"] == 1
    assert "f-docs" not in links


def test_a_github_source_links_its_folder_tree_by_name_even_before_a_sync():
    src = {"type": "github", "projectId": "p-a", "folderPrefix": "Docs/"}
    links = build_links(FOLDERS, PROJECTS, {"p-a": {"f-design": 5}}, [src])
    assert links["f-docs"] == [{"projectId": "p-a", "projectName": "AIPLA", "documents": 0, "via": "github"}]
    assert links["f-design"][0] == {"projectId": "p-a", "projectName": "AIPLA", "documents": 5, "via": "github"}
    assert "f-docsx" not in links


def test_a_folder_source_follows_the_folder_and_optionally_its_subfolders():
    with_subs = {"type": "folder", "projectId": "p-c", "folderId": "f-notes", "includeSubfolders": True}
    links = build_links(FOLDERS, PROJECTS, {}, [with_subs])
    assert links["f-notes"][0]["via"] == "folder" and links["f-notes-old"][0]["via"] == "folder"
    without = {**with_subs, "includeSubfolders": False}
    links = build_links(FOLDERS, PROJECTS, {}, [without])
    assert "f-notes" in links and "f-notes-old" not in links


def test_several_projects_are_sorted_by_name_and_unknown_ones_dropped():
    counts = {"p-c": {"f-loose": 1}, "p-a": {"f-loose": 2}, "p-gone": {"f-loose": 9}}
    links = build_links(FOLDERS, PROJECTS, counts, [])
    assert [r["projectName"] for r in links["f-loose"]] == ["AIPLA", "Clients"]
