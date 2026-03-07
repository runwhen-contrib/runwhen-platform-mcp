# RunWhen Platform MCP

RunWhen Platform MCP Server — exposes workspace chat, issues, SLXs, run sessions, and Tool Builder (script run/commit) to MCP clients (Cursor, Claude Desktop, etc.). Published to PyPI as `runwhen-platform-mcp`.

## Install

Requires Python 3.10+.

```bash
pip install runwhen-platform-mcp
```

Or from source (in a virtual environment):

```bash
git clone https://github.com/runwhen-contrib/runwhen-platform-mcp.git
cd runwhen-platform-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `runwhen-mcp` command inside the venv. When configuring your MCP client, use the full path to the venv entry point so the client can find it without activating the venv:

```bash
# Find the full path
which runwhen-mcp
# e.g. /Users/you/runwhen-platform-mcp/.venv/bin/runwhen-mcp
```

## Run the MCP server

After install, use the `runwhen-mcp` entry point:

```bash
runwhen-mcp
```

Or:

```bash
python -m runwhen_platform_mcp.server
```

## Configure Cursor

Add to `.cursor/mcp.json` (or global MCP settings).

If installed globally or via `pip install runwhen-platform-mcp`:

```json
{
  "mcpServers": {
    "runwhen": {
      "command": "runwhen-mcp",
      "env": {
        "RW_API_URL": "https://papi.beta.runwhen.com",
        "RUNWHEN_TOKEN": "your-jwt-token",
        "DEFAULT_WORKSPACE": "your-workspace"
      }
    }
  }
}
```

If installed from source into a venv, use the full path to the venv's `runwhen-mcp`:

```json
{
  "mcpServers": {
    "runwhen": {
      "command": "/absolute/path/to/runwhen-platform-mcp/.venv/bin/runwhen-mcp",
      "env": {
        "RW_API_URL": "https://papi.beta.runwhen.com",
        "RUNWHEN_TOKEN": "your-jwt-token",
        "DEFAULT_WORKSPACE": "your-workspace"
      }
    }
  }
}
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RW_API_URL` | Yes | RunWhen API base URL (e.g. `https://papi.beta.runwhen.com`). Agent URL is derived from this. |
| `RUNWHEN_TOKEN` | Yes | RunWhen API token (JWT or Personal Access Token). |
| `DEFAULT_WORKSPACE` | No | Default workspace name so tools don't need `workspace_name` every time. |

See `.env.example` in the repo for a template.

## What's in this repo

| Component | Path | Description |
|-----------|------|-------------|
| **MCP Server** | `runwhen_platform_mcp/` | Python package; run via `runwhen-mcp` or `python -m runwhen_platform_mcp.server` |
| **Docs** | `runwhen_platform_mcp/docs/` | Tool Builder flow, RUNWHEN.md template/example |
| **Rules** | `rules/` | Cursor rules for task authoring and infrastructure |
| **Skills** | `skills/` | Skill for building RunWhen tasks end-to-end |
| **Agents** | `agents/` | SRE, task builder, and codecollection author personas |
| **Cursor plugin** | `.cursor-plugin/`, `mcp.json` | Plugin metadata and MCP config for Cursor |

## PyPI release

Releases are built and published to PyPI via GitHub Actions when relevant paths change on `main`. The workflow uses [runwhen-contrib/github-actions/publish-pypi](https://github.com/runwhen-contrib/github-actions) with date-based versioning (`YYYY.MM.DD.N`). Configure `PYPI_TOKEN` (and optionally `SLACK_BOT_TOKEN` / `slack_channel`) in the repo secrets.

## License

Apache-2.0
