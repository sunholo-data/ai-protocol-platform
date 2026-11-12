# Writable project sources

A project can follow a folder in a GitHub repository (a project *source*). When the
project's owner turns on **Let the AI write here** for that source, the agent in the
project's chats can commit text files to it: notes, plans, status, drafts. Each change
is a commit on the source's branch, so people, other agents and other projects see it
through git, and its history is the audit trail.

## Setting it up

Writing uses its own GitHub App, separate from the read-only App that syncs sources.
GitHub itself then limits which repositories can ever be written.

1. Create a GitHub App on the organisation (Settings → Developer settings → GitHub
   Apps → New):
   * Repository permissions: **Contents: Read and write**. Metadata (read) is
     added automatically. Grant nothing else, in particular **not Workflows** (GitHub
     then refuses changes to `.github/workflows/`) or Administration.
   * Webhook: off. Don't request user authorisation (OAuth).
   * Installable: only on this account.
2. Install it with **Only select repositories**, choosing the repositories the AI may
   write.
3. Generate a private key and store it with your other secrets (Secret Manager, or
   your secret store on a local host).
4. Add a write credential to `GITHUB_SOURCE_CREDENTIALS`:

   ```yaml
   credentials:
     docs-write:
       app_id: "<writer app id>"
       private_key_env: GITHUB_WRITER_APP_PRIVATE_KEY
       access: write
       repos: ["example-org/*", "!example-org/private"]
       users: {type: tagged, tags: [staff]}
   ```

   `repos` and `users` work as for read credentials and are checked on every write,
   against the person as they are now. Only a GitHub App can be a write credential.
   Each write gets a one-hour token for that one repository with *Contents: write*.
   A write credential is never offered for syncing, and the GitHub MCP server never
   uses one.

To let the AI write to another repository later, add the repository to the App's
installation (GitHub → the App → Install App → Configure). The project page shows a
source's owner this, with a link, when the App isn't installed on that source's
repository.

## What the agent can do

* `read_project_file(path)`: the exact committed text of a file, and its `version`.
* `write_project_files(files, message)`: one commit with up to 20 whole text files
  (1 MB in total), all in one writable folder. Paths are as the project shows them:
  `<folder>/<file>`. The agent is told to put everything one request changes into
  one call.
* `delete_project_files(paths, message)`: prepares a deletion and returns an approval
  link. Nothing is deleted until the owner approves it on the approval page (or from
  the CLI or an MCP client with approval rights). The files then go in one commit, or
  not at all if any of them changed on GitHub in the meantime.

Rules, enforced on the server:

* Only inside the writable source's folder (its `path`), and only on its branch.
  Commits go straight to the branch, without pull requests.
* Text formats only: Markdown, text, CSV, HTML, LaTeX, and the config, data and code
  formats a source syncs. A PDF's or a Word file's parsed text is never written back.
* **No lost updates.** A file someone changed on GitHub since the agent read it is
  refused, never overwritten. The agent reads it again and redoes its change. The
  branch is never force-pushed.
* At most 30 commits per source per hour.
* Commits are authored by the App's bot. The message ends with a
  `Co-authored-by:` trailer, set by `GITHUB_WRITE_COAUTHOR`: the person in the chat
  (unset, or `person`), a fixed identity for every commit (`Example Platform
  <noreply@example.com>`, so no member's address reaches git), or none (`0`). It then
  has a `Platform-Project:` trailer (when `APP_BASE_URL` is set) and a
  `Platform-Session:` trailer, so a commit can be traced to its chat inside the
  platform.

What is written appears in the project at once, without waiting for a sync. The next
sync finds those files unchanged.

Turning writing on is a switch on the source's row, or on the add form when you
have write access.

**Moves keep their document.** When a sync finds that the same file (the same bytes,
the same type) left one path and appeared at another, the document moves with it.
It keeps its id, its links from chats and its parsed text, and isn't downloaded or
parsed again. This applies to every project source, writable or not. A preview lists
moves as `old → new`.

Design: `docs/design/private/project-source-write.md` (withheld from the public
export).
