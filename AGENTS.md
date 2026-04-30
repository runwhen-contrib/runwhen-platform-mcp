# AGENTS.md — RunWhen Platform MCP Server

This repository provides an MCP server and Cursor plugin for the RunWhen AI SRE
platform. Three agent personas interact with the platform through MCP tools:

- **`runwhen-sre`** — Investigates and troubleshoots infrastructure by querying the platform for issues, run sessions, and production insights.
- **`runwhen-task-builder`** — Builds workspace-specific health checks and automation tasks by analyzing code and infrastructure, then testing and committing SLXs.
- **`runwhen-codecollection-author`** — Builds reusable, parameterized codebundles that work across any environment. Tests them as SLXs in a workspace and cleans up after validation.

## Authentication

- **Local (stdio)** — The MCP client passes **`RUNWHEN_TOKEN`** (and **`RW_API_URL`**) in environment variables; there is no OAuth handshake in-process.
- **Remote (HTTP)** — Each request carries auth: usually **`Authorization: Bearer`** with a JWT or Personal Access Token. When the server is deployed with **`MCP_BASE_URL`** and **`MCP_PAPI_OAUTH_CLIENT_ID`** / **`MCP_PAPI_OAUTH_CLIENT_SECRET`**, MCP clients that support remote OAuth can sign in via the browser; discovery is at **`{MCP_BASE_URL}/.well-known/oauth-authorization-server`**, and the upstream OAuth app must allow redirect **`{MCP_BASE_URL}/auth/callback`**. Configure these variables on the **server** (not in the agent’s MCP client env). See the repo README section **OAuth for remote HTTP deployments**.

## Important: `workspace_name` is required

Most tools require a `workspace_name` parameter. **Always provide it explicitly** —
do not omit it or rely on defaults. If you don't know the workspace name, call
`list_workspaces` first to discover available workspaces.

## Tool Routing — `workspace_chat` vs direct tools

`workspace_chat` is the **primary investigation tool**. It has internal access
to ~25+ tools including semantic search, keyword grep, resource graph traversal,
knowledge base lookup, and data analysis. It produces **materially better
answers** than combining multiple direct API calls for investigative questions.

### ALWAYS prefer `workspace_chat` for:

- Questions about specific topics — *"issues related to neo4j"*
- Investigations across domains — *"what's failing in the watcher namespace?"*
- Searching by keyword or context — *"find health checks for postgres"*
- Multi-step analysis — *"correlate recent failures with deployment changes"*
- Any question a knowledgeable human would answer by searching and interpreting

Responses include a `chatUrl` the user can open to continue the session in the
RunWhen UI (e.g. to run tasks from the chat).

### ALWAYS use direct tools for:

| Need | Tool(s) |
|------|---------|
| **Execute** a task | `run_slx` (workspace_chat CANNOT run tasks) |
| Task authoring | `validate_script`, `run_script_and_wait`, `commit_slx`, `delete_slx` |
| Registry | `search_registry`, `get_registry_codebundle`, `deploy_registry_codebundle` |
| Chat config CRUD | `list/get/create/update_chat_rule`, `list/get/create/update_chat_command` |
| KB mutations | `create/update/delete_knowledge_base_article` |
| Workspace discovery | `list_workspaces` |
| Runner config | `get_workspace_secrets`, `get_workspace_locations` (location auto-resolves; only needed for multi-runner disambiguation) |
| Local context | `get_workspace_context` (reads RUNWHEN.md) |

### Overlapping read/query tools (use sparingly)

The following tools return **raw structured JSON** from PAPI. `workspace_chat`
can answer the same questions internally with richer context. Use these **only**
when you need raw JSON for programmatic processing (counting, field filtering,
feeding into code) — **not** for user-facing answers:

`get_workspace_issues`, `get_workspace_slxs`, `get_run_sessions`,
`get_workspace_config_index`, `get_issue_details`, `get_slx_runbook`,
`search_workspace`, `list_knowledge_base_articles`, `get_knowledge_base_article`

## MCP Tools

The RunWhen MCP server exposes the following tools. Agents should use these
to interact with the platform — do not attempt to call APIs directly.

### Workspace Intelligence

| Tool | Description |
|------|-------------|
| `workspace_chat` | **Primary investigation tool.** Ask the RunWhen AI assistant about your infrastructure (issues, tasks, resources, knowledge base). Responses include `chatUrl` (open this session in the RunWhen UI to run tasks). Set `RUNWHEN_APP_URL` when `RW_API_URL` is an internal/cluster URL. |
| `list_workspaces` | List all workspaces you have access to |
| `get_workspace_chat_config` | Get resolved chat rules and commands for a workspace (metadata only) |
| `list_chat_rules`, `get_chat_rule`, `create_chat_rule`, `update_chat_rule` | List, get, create, update workspace chat rules |
| `list_chat_commands`, `get_chat_command`, `create_chat_command`, `update_chat_command` | List, get, create, update workspace chat commands (slash-commands) |
| `get_workspace_issues` | Raw JSON: current issues (prefer `workspace_chat` for search/investigation) |
| `get_workspace_slxs` | Raw JSON: list SLXs (prefer `workspace_chat` for search/investigation) |
| `get_run_sessions` | Raw JSON: recent run sessions (prefer `workspace_chat` for search/investigation) |
| `get_workspace_config_index` | Raw JSON: workspace config overview (prefer `workspace_chat` for questions) |
| `get_issue_details` | Raw JSON: issue by ID (prefer `workspace_chat` for investigation) |
| `get_slx_runbook` | Raw JSON: SLX runbook (prefer `workspace_chat` for "what does this do?") |
| `search_workspace` | Autocomplete search (prefer `workspace_chat` for richer results) |

### Task Authoring (Tool Builder)

| Tool | Description |
|------|-------------|
| `get_workspace_context` | Load domain-specific rules from the project's RUNWHEN.md — **call this before writing any script** |
| `validate_script` | Validate a script against the RunWhen contract (main function, output format) |
| `run_script` | Execute a script on a RunWhen runner and get a run ID |
| `get_run_status` | Poll the status of a running script (RUNNING, SUCCEEDED, FAILED) |
| `get_run_output` | Get parsed output from a completed run (issues, stdout, stderr, report) |
| `run_script_and_wait` | Execute a script and wait for results — combines run + poll + output in one call |
| `commit_slx` | Commit a tested script as an SLX to the workspace repo (task + optional SLI) |
| `get_workspace_secrets` | List available secret key names for a workspace |
| `get_workspace_locations` | List available runner locations for script execution |

## Task Authoring Workflow

Always follow this sequence when building a new task:

1. **Load context** — `get_workspace_context` reads the RUNWHEN.md file
2. **Write script** — Follow the contract and RUNWHEN.md rules
3. **Validate** — `validate_script` checks compliance
4. **Discover secrets** — `get_workspace_secrets` (location auto-resolves — only call `get_workspace_locations` when multiple locations exist and you need to choose)
5. **Test** — `run_script_and_wait` executes against live infrastructure
6. **Iterate** — Fix based on output, re-test
7. **Commit** — `commit_slx` writes the SLX to the workspace repo

## Script Contract

### Python Task
- Define `main()` returning `List[Dict]` with keys: `issue title`, `issue description`, `issue severity` (int 1-4), `issue next steps`
- Do NOT call `main()` directly or use `if __name__ == "__main__"`
- Access config via `os.environ`; secrets are file paths (e.g. `KUBECONFIG = os.environ["kubeconfig"]`)

### Python SLI
- Define `main()` returning a float between 0 and 1

### Bash Task
- Define `main()` and write issue JSON array to FD 3 (`>&3`)
- Use `jq` for reliable JSON construction

### Bash SLI
- Define `main()` and write a float to FD 3

## Required Tags

Every SLX committed via `commit_slx` must include:
- **`access`**: `read-write` (modifies resources) or `read-only` (inspection only)
- **`data`**: `logs-bulk` (command output), `config` (configuration data), or `logs-stacktrace` (stack traces)

## Custom Platform Rule for resourcePath and hierarchy

**All tasks built via the MCP server must use `custom/` as the platform prefix
in `resource_path`.** The server enforces this automatically — if a
`resource_path` does not start with `custom/`, the prefix is prepended.

This prevents MCP-authored tasks from colliding with platform-managed resources
(e.g. those discovered by runwhen-local) in the UI and search index.

| Parameter | Requirement |
|-----------|-------------|
| `resource_path` | Must start with `custom/` (auto-enforced). Example: `custom/kubernetes/cluster-01/prod-ns` |
| `hierarchy` | Should start with `["platform", ...]` with a corresponding tag `{"name": "platform", "value": "custom"}` |
| `tags` | Must include `{"name": "platform", "value": "custom"}` for hierarchy resolution |

**Never place a custom task under the resource path of an existing
platform-managed resource.** If an existing resource lives at
`kubernetes/cluster-01/prod-ns`, the custom task must be at
`custom/kubernetes/cluster-01/prod-ns` — not the bare path.

## RUNWHEN.md

If a `RUNWHEN.md` file exists in the project, it contains domain-specific rules
that **override general defaults**. Always load it via `get_workspace_context`
before writing scripts. It may specify:
- Which database replicas to target
- Required kubectl flags
- Naming conventions for SLXs
- Severity guidelines
- Known infrastructure gotchas
