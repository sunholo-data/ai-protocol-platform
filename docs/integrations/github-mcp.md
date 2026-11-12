# GitHub MCP server

Agents can read GitHub through GitHub's hosted MCP server: commits, branches,
files, tags, releases, and code search. The deployment's GitHub App does the
reading, under the same access rules as GitHub sources (`GITHUB_SOURCE_CREDENTIALS`,
see `backend/sources/credentials.py`). Nobody signs in to GitHub, and no new
secret is stored.

## How access works

An `mcp_servers` row with `auth: "github_app"` gets a fresh header on each tool
call: a GitHub App installation token minted for the person in the chat
(`backend/tools/mcp/github_app_auth.py`).

- Only App credentials whose `users` rule admits that person count. Credentials
  that use a personal token (`token_env`) are never used here.
- The token names every repository the installation can see that one of those
  credentials `covers`. A `!owner/name` exclusion holds, so a repository kept
  from staff stays unreadable through the MCP server too: GitHub answers 404.
- Permissions are read-only. The default is `contents: read`, which covers
  commits, branches, files, tags, releases and code search. A row may ask for
  other read permissions the App has, such as `pull_requests` or `issues`. Any
  level other than `read` is refused.
- Tokens last an hour and are cached in memory until five minutes before they
  expire. The same person gets the same token, so ADK keeps reusing one MCP
  session.

A person with no matching credential gets a clear error ("You don't have access
to any GitHub repositories on this platform"), not an empty tool list. Restrict
the skill to the same people as the credentials.

## Set it up

1. Configure a GitHub App credential in `GITHUB_SOURCE_CREDENTIALS` (the same one
   GitHub sources use). Its `repos` and `users` decide who reads what.
2. Add the registry row. Point it at the **read-only** endpoint:

   ```json
   {
     "name": "GitHub (read-only)",
     "url": "https://api.githubcopilot.com/mcp/readonly",
     "transport": "http",
     "headers": {},
     "auth": "github_app",
     "operated_by": "github",
     "tags": ["github", "code"]
   }
   ```

   Optional fields:
   - `credentials: ["ref", ...]` uses only those credentials.
   - `permissions: {"contents": "read", "pull_requests": "read"}` asks for
     more read access.
   - `installation_id` chooses one GitHub account when a person's credentials
     span several. A token belongs to one installation.
3. List the server id in `backend/config/fork_mcp_servers.txt` if your own
   tooling seeds the row.
4. Give a skill `tools: [mcp]` and `toolConfigs.mcp.servers: [github]`, with the
   same access rule as the credential.

## The GitHub tab

Add `"workbench": "github"` to the row to show what the agent reads on a
**GitHub** tab of the workbench (`backend/adk/a2ui_github_render.py`). The tab
is drawn from the tool results, never by the model, so give the skill
`toolConfigs.a2ui.enabled: false`.

- It shows commit lists and single commits, pull request and issue lists, a
  pull request or issue with its checks, reviews, files and comments, branches,
  tags, releases, files and folders, and code and repository search.
- There is one tab, updated in place. A pull request or issue builds up while
  the agent stays on it, and an "Earlier in this chat" list links what came
  before.
- It renders *before* the large-output offload (`register(..., offload=True)`).
  The tab gets the full result, and the model still gets the usual artifact
  pointer for a large one.
- Text from GitHub is escaped before it reaches markdown, and only http(s)
  links are made.

## Limits

- GitHub caps a token at 500 repositories. A credential that covers more is
  refused with a message; narrow its `repos`.
- The App's own permissions are the ceiling. Asking for `pull_requests: read`
  when the App lacks it fails when the token is minted.
- The frontend MCP proxy (`protocols/mcp_proxy.py`, used for MCP App iframes)
  does not mint these tokens. The GitHub server has no UI, so only the agent
  calls it.

## Per-user GitHub sign-in

This setup does not use each person's own GitHub account. The platform has no
outbound per-user OAuth for MCP servers yet. That would mean a connect flow, an
encrypted per-user token store, refresh, and revocation. It suits servers that
must act as the person, such as writing on their behalf or reading what only
they can see. For reading the organisation's repositories under the platform's
access rules, the App token is simpler and holds nothing long-lived.
