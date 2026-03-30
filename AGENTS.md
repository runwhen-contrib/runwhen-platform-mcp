# AGENTS.md — RunWhen Platform MCP Server

This repository provides an MCP server and Cursor plugin for the RunWhen AI SRE
platform. Three agent personas interact with the platform through MCP tools:

- **`runwhen-sre`** — Investigates and troubleshoots infrastructure by querying the platform for issues, run sessions, and production insights.
- **`runwhen-task-builder`** — Builds workspace-specific health checks and automation tasks by analyzing code and infrastructure, then testing and committing SLXs.
- **`runwhen-codecollection-author`** — Builds reusable, parameterized codebundles that work across any environment. Tests them as SLXs in a workspace and cleans up after validation.

## Important: `workspace_name` is required

Most tools require a `workspace_name` parameter. **Always provide it explicitly** —
do not omit it or rely on defaults. If you don't know the workspace name, call
`list_workspaces` first to discover available workspaces.

## MCP Tools

The RunWhen MCP server exposes the following tools. Agents should use these
to interact with the platform — do not attempt to call APIs directly.

### Workspace Intelligence

| Tool | Description |
|------|-------------|
| `workspace_chat` | Ask the RunWhen AI assistant about your infrastructure (issues, tasks, resources, knowledge base) |
| `list_workspaces` | List all workspaces you have access to |
| `get_workspace_chat_config` | Get resolved chat rules and commands for a workspace (metadata only) |
| `list_chat_rules`, `get_chat_rule`, `create_chat_rule`, `update_chat_rule` | List, get, create, update workspace chat rules |
| `list_chat_commands`, `get_chat_command`, `create_chat_command`, `update_chat_command` | List, get, create, update workspace chat commands (slash-commands) |
| `get_workspace_issues` | Get current issues for a workspace (filter by severity) |
| `get_workspace_slxs` | List SLXs (health checks / tasks) in a workspace |
| `get_run_sessions` | Get recent run session results |
| `get_workspace_config_index` | Get workspace configuration overview and resource relationships |
| `get_issue_details` | Get detailed information about a specific issue |
| `get_slx_runbook` | Get a specific SLX's runbook definition |
| `search_workspace` | Search tasks, resources, and config by keyword |

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
4. **Discover config** — `get_workspace_secrets` + `get_workspace_locations`
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

## RUNWHEN.md

If a `RUNWHEN.md` file exists in the project, it contains domain-specific rules
that **override general defaults**. Always load it via `get_workspace_context`
before writing scripts. It may specify:
- Which database replicas to target
- Required kubectl flags
- Naming conventions for SLXs
- Severity guidelines
- Known infrastructure gotchas
