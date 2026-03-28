"""Tests for MCP tool registration and schema integrity.

Validates that all registered tools have proper descriptions, no name
collisions, and well-formed parameter schemas.
"""

import asyncio
import re

import pytest

from runwhen_platform_mcp.server import mcp

EXPECTED_TOOLS = {
    "workspace_chat",
    "list_workspaces",
    "get_workspace_chat_config",
    "list_chat_rules",
    "get_chat_rule",
    "create_chat_rule",
    "update_chat_rule",
    "list_chat_commands",
    "get_chat_command",
    "create_chat_command",
    "update_chat_command",
    "get_workspace_issues",
    "get_workspace_slxs",
    "get_run_sessions",
    "get_workspace_config_index",
    "get_issue_details",
    "get_slx_runbook",
    "search_workspace",
    "list_knowledge_base_articles",
    "get_knowledge_base_article",
    "create_knowledge_base_article",
    "update_knowledge_base_article",
    "delete_knowledge_base_article",
    "search_registry",
    "get_registry_codebundle",
    "get_workspace_context",
    "get_workspace_secrets",
    "get_workspace_locations",
    "validate_script",
    "run_script",
    "get_run_status",
    "get_run_output",
    "run_script_and_wait",
    "run_slx",
    "commit_slx",
    "delete_slx",
}


def _get_tools():
    """Load the tool list, creating an event loop if needed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, mcp.list_tools()).result()
    return asyncio.run(mcp.list_tools())


@pytest.fixture(scope="module")
def tools():
    return _get_tools()


def test_no_duplicate_tool_names(tools) -> None:
    names = [t.name for t in tools]
    dupes = [n for n in names if names.count(n) > 1]
    assert len(names) == len(set(names)), f"Duplicate tool names: {dupes}"


def test_all_expected_tools_registered(tools) -> None:
    registered = {t.name for t in tools}
    missing = EXPECTED_TOOLS - registered
    assert not missing, f"Expected tools not registered: {missing}"


def test_no_unexpected_tools(tools) -> None:
    """Catch accidentally registered tools -- update EXPECTED_TOOLS."""
    registered = {t.name for t in tools}
    extra = registered - EXPECTED_TOOLS
    assert not extra, f"Unexpected tools (update EXPECTED_TOOLS if intentional): {extra}"


def test_all_tools_have_descriptions(tools) -> None:
    missing = [t.name for t in tools if not t.description or len(t.description.strip()) < 10]
    assert not missing, f"Tools with missing/short descriptions: {missing}"


def test_tool_names_are_snake_case(tools) -> None:
    bad = [t.name for t in tools if not re.match(r"^[a-z][a-z0-9_]*$", t.name)]
    assert not bad, f"Tool names not snake_case: {bad}"


@pytest.mark.parametrize(
    "tool_name",
    [
        "workspace_chat",
        "run_script",
        "commit_slx",
        "delete_slx",
        "validate_script",
    ],
)
def test_key_tools_have_parameters(tools, tool_name) -> None:
    tool = next((t for t in tools if t.name == tool_name), None)
    assert tool is not None, f"Tool {tool_name} not found"
    params = tool.parameters or {}
    props = params.get("properties", {}) if isinstance(params, dict) else {}
    assert len(props) > 0, f"Tool {tool_name} should have parameters"
