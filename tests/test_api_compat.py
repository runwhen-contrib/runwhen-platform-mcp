"""Tests for FastAPI backend compatibility (RW-337) and schema correctness.

Validates that tool parameter schemas match what the FastAPI backend expects,
catching mismatches before they become 404s or silent failures.
"""

import asyncio

import pytest

from runwhen_platform_mcp.server import mcp


def _get_tools():
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


def _find_tool(tools, name):
    return next((t for t in tools if t.name == name), None)


class TestScriptPathSchema:
    """run_script, run_script_and_wait, commit_slx should accept script_path."""

    @pytest.mark.parametrize(
        "tool_name",
        ["run_script", "run_script_and_wait", "commit_slx"],
    )
    def test_has_script_path_param(self, tools, tool_name) -> None:
        tool = _find_tool(tools, tool_name)
        assert tool is not None, f"{tool_name} not found"
        props = tool.parameters.get("properties", {})
        assert "script_path" in props, f"{tool_name} should have script_path parameter"

    def test_commit_slx_has_sli_script_path(self, tools) -> None:
        tool = _find_tool(tools, "commit_slx")
        assert tool is not None
        props = tool.parameters.get("properties", {})
        assert "sli_script_path" in props

    @pytest.mark.parametrize(
        "tool_name",
        ["run_script", "run_script_and_wait", "commit_slx"],
    )
    def test_script_not_required_when_path_available(self, tools, tool_name) -> None:
        """script should be optional so script_path can be used instead."""
        tool = _find_tool(tools, tool_name)
        required = tool.parameters.get("required", [])
        assert "script" not in required, (
            f"{tool_name}: 'script' should not be required (script_path is an alternative)"
        )


class TestToolParameterTypes:
    """Verify parameter types match what the FastAPI backend accepts."""

    def test_run_sessions_limit_param(self, tools) -> None:
        tool = _find_tool(tools, "get_run_sessions")
        assert tool is not None
        props = tool.parameters.get("properties", {})
        assert "limit" in props

    def test_search_workspace_has_query(self, tools) -> None:
        tool = _find_tool(tools, "search_workspace")
        assert tool is not None
        props = tool.parameters.get("properties", {})
        assert "query" in props

    def test_commit_slx_has_all_required_params(self, tools) -> None:
        tool = _find_tool(tools, "commit_slx")
        assert tool is not None
        props = tool.parameters.get("properties", {})
        for expected in ("slx_name", "alias", "statement", "location", "interpreter"):
            assert expected in props, f"commit_slx missing param: {expected}"

    def test_delete_slx_params(self, tools) -> None:
        tool = _find_tool(tools, "delete_slx")
        assert tool is not None
        props = tool.parameters.get("properties", {})
        assert "slx_name" in props
        assert "branch" in props


class TestRunSlxSchema:
    """run_slx should have correct parameters matching the RunRequest API."""

    def test_registered(self, tools) -> None:
        tool = _find_tool(tools, "run_slx")
        assert tool is not None

    def test_has_slx_name_param(self, tools) -> None:
        tool = _find_tool(tools, "run_slx")
        props = tool.parameters.get("properties", {})
        assert "slx_name" in props

    def test_has_task_titles_param(self, tools) -> None:
        tool = _find_tool(tools, "run_slx")
        props = tool.parameters.get("properties", {})
        assert "task_titles" in props

    def test_slx_name_is_required(self, tools) -> None:
        tool = _find_tool(tools, "run_slx")
        required = tool.parameters.get("required", [])
        assert "slx_name" in required


class TestKnowledgeBaseSchema:
    """KB tools should have correct parameters matching the Notes API."""

    KB_TOOLS = [
        "list_knowledge_base_articles",
        "get_knowledge_base_article",
        "create_knowledge_base_article",
        "update_knowledge_base_article",
        "delete_knowledge_base_article",
    ]

    @pytest.mark.parametrize("tool_name", KB_TOOLS)
    def test_kb_tool_registered(self, tools, tool_name) -> None:
        tool = _find_tool(tools, tool_name)
        assert tool is not None, f"KB tool {tool_name} not registered"

    def test_create_has_content_param(self, tools) -> None:
        tool = _find_tool(tools, "create_knowledge_base_article")
        props = tool.parameters.get("properties", {})
        assert "content" in props
        assert "resource_paths" in props
        assert "abstract_entities" in props

    def test_update_has_partial_params(self, tools) -> None:
        tool = _find_tool(tools, "update_knowledge_base_article")
        props = tool.parameters.get("properties", {})
        assert "note_id" in props
        assert "content" in props
        assert "status" in props
        assert "verified" in props

    def test_list_has_filter_params(self, tools) -> None:
        tool = _find_tool(tools, "list_knowledge_base_articles")
        props = tool.parameters.get("properties", {})
        assert "search" in props
        assert "limit" in props

    def test_delete_requires_note_id(self, tools) -> None:
        tool = _find_tool(tools, "delete_knowledge_base_article")
        props = tool.parameters.get("properties", {})
        assert "note_id" in props
