# RunWhen MCP Server — Cross-LLM Testing Plan

## Problem

Different LLM models (Claude Sonnet, Claude Opus, GPT-4o, Cursor Auto mode, Gemini)
interpret MCP tool schemas differently. A tool call that works perfectly with Sonnet
may produce malformed arguments with Auto mode or GPT-4o.

## Testing Matrix

### Clients

| Client         | Config Method   | Transport |
|----------------|-----------------|-----------|
| Cursor IDE     | mcp.json (stdio) | stdio    |
| Claude Desktop | claude_desktop_config.json | stdio |
| Claude Code    | claude_code_config.json | stdio |
| Custom HTTP    | Bearer token    | http      |

### Models (Cursor-specific)

| Model Label    | Notes                              |
|----------------|------------------------------------|
| Auto           | Cursor's automatic model selection |
| claude-sonnet  | Most reliable baseline             |
| claude-opus    | High-capability, verbose           |
| gpt-4o         | OpenAI, different schema parsing   |
| gemini-2.5-pro | Google, different tool call format  |

## Test Scenarios

### Tier 1 — Core Functionality (must pass on all models)

1. **workspace_chat** — Send a simple question, get a response
2. **get_workspace_issues** — List issues (no params beyond workspace)
3. **get_workspace_slxs** — List SLXs
4. **search_workspace** — Search with a query string
5. **get_run_sessions** — List recent run sessions

### Tier 2 — Complex Parameters (key risk area)

6. **run_script** with inline `script` parameter
7. **run_script** with `script_path` parameter
8. **commit_slx** with all required fields
9. **commit_slx** with `script_path` and `sli_script_path`
10. **validate_script** with multi-line script content

### Tier 3 — Edge Cases

11. **commit_slx** with `tags` (list of dicts — common failure point)
12. **commit_slx** with both `sli_script` + error case
13. **run_script** with `env_vars` and `secret_vars` (nested dicts)
14. **Multi-tool orchestration** — run_script → get_run_status → get_run_output

## Known Failure Patterns

| Pattern                        | Affected Models     | Workaround                    |
|--------------------------------|---------------------|-------------------------------|
| Nested dict params malformed   | GPT-4o, Auto        | Flatten or simplify schema    |
| List of dicts serialized wrong | Gemini              | Accept JSON string fallback   |
| Very long string params        | All (token limits)  | Use script_path               |
| Optional params sent as null   | Some Auto models    | Ensure None handling in tools |
| Tool name confusion with multi-MCP | All            | MCP_SERVER_LABEL, clear names |

## Test Execution Process

### Manual Testing (current)

```bash
# For each client/model combination:
# 1. Configure MCP server
# 2. Run each tier 1-3 scenario
# 3. Record: pass/fail, error message, model version
```

### Automated Schema Testing (in repo)

```bash
pytest tests/test_tool_schema.py -v     # Schema integrity
pytest tests/test_api_compat.py -v      # FastAPI compatibility
pytest tests/test_resolve_script.py -v  # script_path support
pytest tests/test_validation.py -v      # Script contract validation
```

### Future: Integration Test Harness

Goal: Automated test runner that:
1. Starts the MCP server in stdio mode
2. Sends tool calls with known-good parameters via JSON-RPC
3. Validates response structure (not content — no live API)
4. Can be run in CI for regression catching

Implementation approach:
- Use `fastmcp`'s test client or raw JSON-RPC over subprocess stdin/stdout
- Mock PAPI responses with httpx mock transport
- Test each tool with representative parameter combinations
- Run against each new release before publishing

## Success Criteria

- All Tier 1 scenarios pass on Claude Sonnet, Claude Opus, GPT-4o
- All Tier 2 scenarios pass on Claude Sonnet and Claude Opus
- Script_path workaround documented for Tier 2 failures on other models
- Schema tests in CI catch regressions before release
