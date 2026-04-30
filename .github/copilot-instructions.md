# RunWhen Platform MCP Server — Copilot Instructions

This repository provides an MCP server for the RunWhen AI SRE platform.
Use the MCP tools to investigate infrastructure, build automation tasks,
and manage workspace configuration.

## `workspace_name` is required

Most tools require a `workspace_name` parameter. **Always provide it.**
If unknown, call `list_workspaces` first.

## Tool Routing

**`workspace_chat`** is the primary investigation tool — prefer it for any
question that involves searching, correlating, or interpreting infrastructure
data. It has internal semantic search, keyword grep, and resource graph
traversal that produce better answers than combining direct API calls.

Use direct tools only for: executing tasks (`run_slx`), task authoring
(`validate_script`, `run_script_and_wait`, `commit_slx`), registry
operations, chat config CRUD, KB mutations, and runner configuration.

## Available Skills

This project includes detailed workflow skills in `skills/` (also at `.github/skills/`).
Use these for multi-step tasks — they contain step-by-step instructions,
templates, and examples.

| Skill | When to use |
|-------|-------------|
| `build-runwhen-task` | Building a new health check or automation task |
| `find-and-deploy-codebundle` | Deploying a pre-built codebundle from the registry |
| `run-existing-slx` | Running an existing SLX in a workspace |
| `verify-mcp-setup` | Validating the MCP server is installed and working |
| `manage-rules` | Creating or updating workspace chat rules |
| `manage-commands` | Creating or updating workspace chat commands |
| `manage-knowledge` | Creating or updating knowledge base articles |
| `discover-secrets` | Finding available workspace secrets |
| `discover-locations` | Finding available runner locations |
| `configure-resource-path` | Setting resource paths for SLX indexing |
| `configure-hierarchy` | Organizing SLXs with hierarchy tags |
| `build-operational-context` | Building a RUNWHEN.md file for a project |
| `decompose-skill-for-runwhen` | Assessing an existing skill / agent / runbook and proposing a RunWhen decomposition (working draft) |

## Script Contract (quick reference)

- **Python task**: `main()` → `List[Dict]` with keys `issue title`, `issue description`, `issue severity` (1-4), `issue next steps`
- **Bash task**: `main()` writes issue JSON array to FD 3 (`>&3`)
- **SLI** (Python or Bash): `main()` returns/writes a float 0–1
- Always call `get_workspace_context` before writing scripts
