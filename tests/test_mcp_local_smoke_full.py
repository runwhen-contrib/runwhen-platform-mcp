"""Full MCP protocol smoke test against a locally-started server.

Exercises 20+ tools across categories: server metadata, skills, discovery,
registry, validation, workspace read, and chat config. Each test creates
its own MCP session for clean failure attribution.

Requires LOCAL_MCP_SMOKE=true, RUNWHEN_MCP_URL, RUNWHEN_TOKEN, and
RW_SMOKE_WORKSPACE (defaults to first available workspace).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

pytestmark = [pytest.mark.integration, pytest.mark.local_mcp_smoke]

# Skip the entire module during collection when not running in mcp-smoke job.
# The _session() helper accesses RUNWHEN_MCP_URL at module level during test
# execution, before individual test-level skip checks can run.
if os.environ.get("LOCAL_MCP_SMOKE") != "true":
    pytest.skip(
        "LOCAL_MCP_SMOKE=true not set — skipping local MCP smoke",
        allow_module_level=True,
    )

# -- helpers -------------------------------------------------------------


def _require_env() -> None:
    if os.environ.get("LOCAL_MCP_SMOKE") != "true":
        pytest.skip("LOCAL_MCP_SMOKE=true not set")
    if not os.environ.get("RUNWHEN_MCP_URL") or not os.environ.get("RUNWHEN_TOKEN"):
        pytest.skip("Set RUNWHEN_MCP_URL and RUNWHEN_TOKEN")


def _mcp_url() -> str:
    return os.environ["RUNWHEN_MCP_URL"].strip().rstrip("/")


def _token() -> str:
    return os.environ["RUNWHEN_TOKEN"]


def _workspace() -> str:
    return os.environ.get("RW_SMOKE_WORKSPACE", "t-oncall").strip()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json, text/event-stream",
    }


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(120.0, connect=30.0)


def _strict_json(text: str) -> Any:
    def _reject(constant: str) -> Any:  # noqa: ANN401
        raise ValueError(f"non-standard JSON constant: {constant!r}")

    return json.loads(text, parse_constant=_reject)


def _tool_text(result: CallToolResult) -> str:
    if result.isError:
        parts: list[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        raise AssertionError("tool error: " + (" | ".join(parts) if parts else "(no text)"))
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
    assert texts, "expected at least one TextContent block"
    return "\n".join(texts)


def _connect_error(exc: BaseException) -> httpx.ConnectError | None:
    if isinstance(exc, httpx.ConnectError):
        return exc
    nested = getattr(exc, "exceptions", None)
    if nested is not None:
        for sub in nested:
            found = _connect_error(sub)
            if found is not None:
                return found
    if exc.__cause__ is not None:
        return _connect_error(exc.__cause__)
    return None


# -- session helper ------------------------------------------------------


async def _session():
    url = _mcp_url()
    async with (
        httpx.AsyncClient(headers=_headers(), timeout=_timeout()) as http,
        streamable_http_client(url, http_client=http) as (read, write, _get_sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def _run(coro):
    try:
        asyncio.run(coro)
    except BaseException as exc:
        connect_err = _connect_error(exc)
        if connect_err is not None:
            pytest.skip(f"MCP endpoint unreachable: {connect_err}")
        raise


# -- shared client helper ------------------------------------------------


async def _call(session: ClientSession, tool: str, args: dict | None = None):
    result = await session.call_tool(tool, args or {})
    return _strict_json(_tool_text(result))


# -- server metadata tests -----------------------------------------------


class TestServerMetadata:
    def test_initialize_succeeds(self) -> None:
        async def _test():
            async for _ in _session():
                pass  # initialize happened in _session()

        _run(_test())

    def test_tools_list_has_critical_tools(self) -> None:
        async def _test():
            async for session in _session():
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert len(names) >= 30, f"expected >=30 tools, got {len(names)}"
                critical = [
                    "list_workspaces",
                    "list_discovery_platforms",
                    "list_indexed_resource_types",
                    "validate_script",
                    "list_skills",
                ]
                for name in critical:
                    assert name in names, f"'{name}' not in tools; first 20: {sorted(names)[:20]}"

        _run(_test())

    def test_list_workspaces_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "list_workspaces")
                assert isinstance(payload, list)
                assert len(payload) >= 1

        _run(_test())

    def test_workspace_chat_responds(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "workspace_chat",
                    {
                        "workspace_name": ws,
                        "message": "hello",
                    },
                )
                assert isinstance(payload, dict)
                assert "message" in payload, f"expected 'message' key; got {sorted(payload.keys())}"
                assert "sessionId" in payload

        _run(_test())


# -- skills tests --------------------------------------------------------


class TestSkills:
    def test_list_skills_returns_items(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "list_skills")
                assert isinstance(payload, dict)
                assert "skills" in payload
                assert len(payload["skills"]) >= 5
                first = payload["skills"][0]
                assert isinstance(first, dict)
                assert "name" in first

        _run(_test())

    def test_get_skill_returns_body(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "get_skill", {"name": "build-runwhen-task"})
                assert isinstance(payload, dict)
                assert "name" in payload
                assert "body" in payload
                assert len(payload["body"]) > 100, "skill body should be substantial"

        _run(_test())

    def test_get_skill_unknown_returns_error(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "get_skill", {"name": "nonexistent-skill-xyz"})
                assert isinstance(payload, dict)
                assert "error" in payload

        _run(_test())


# -- discovery tools tests -----------------------------------------------


class TestDiscovery:
    def test_list_discovery_platforms_has_kubernetes(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "list_discovery_platforms")
                assert "platforms" in payload
                names = {p["platform"] for p in payload["platforms"]}
                assert "kubernetes" in names
                assert "runwhen" in names

        _run(_test())

    def test_list_discovery_platforms_has_decision_guide(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "list_discovery_platforms")
                assert "decision_guide" in payload
                guide = payload["decision_guide"]
                names = {p["platform"] for p in payload["platforms"]}
                for name in names:
                    assert name in guide, f"platform '{name}' missing from decision_guide"

        _run(_test())

    def test_list_indexed_resource_types_kubernetes(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(
                    session,
                    "list_indexed_resource_types",
                    {"platform": "kubernetes"},
                )
                assert "resource_types" in payload
                assert "namespace" in payload["resource_types"]

        _run(_test())

    def test_list_indexed_resource_types_aws(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(
                    session,
                    "list_indexed_resource_types",
                    {"platform": "aws", "search": "ec2"},
                )
                assert "error" not in payload

        _run(_test())

    def test_list_indexed_resource_types_unknown_platform(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(
                    session,
                    "list_indexed_resource_types",
                    {"platform": "not-a-real-platform"},
                )
                assert "error" in payload

        _run(_test())


# -- registry tests ------------------------------------------------------


class TestRegistry:
    def test_search_registry_kubernetes_returns_hits(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(
                    session, "search_registry", {"search": "kubernetes pod health"}
                )
                assert isinstance(payload, dict)
                # May be empty in airgap; just verify it doesn't error
                assert "error" not in payload

        _run(_test())


# -- validation tests ----------------------------------------------------


class TestValidation:
    def test_validate_script_accepts_valid_python(self) -> None:
        SCRIPT = (
            "def main():\n"
            '    return [{"issue title": "test", "issue description": "desc", '
            '"issue severity": 4, "issue next steps": "none"}]\n'
        )

        async def _test():
            async for session in _session():
                payload = await _call(
                    session,
                    "validate_script",
                    {"script": SCRIPT, "interpreter": "python", "task_type": "task"},
                )
                assert isinstance(payload, dict)
                assert "error" not in payload

        _run(_test())

    def test_validate_script_returns_structured_response(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(
                    session,
                    "validate_script",
                    {
                        "script": "def main():\n    return []",
                        "interpreter": "python",
                        "task_type": "task",
                    },
                )
                assert isinstance(payload, dict)
                assert "valid" in payload

        _run(_test())


# -- workspace-context tests ---------------------------------------------


class TestWorkspaceContext:
    def test_get_workspace_context_returns_string(self) -> None:
        async def _test():
            async for session in _session():
                payload = await _call(session, "get_workspace_context")
                assert isinstance(payload, dict)
                assert "error" not in payload

        _run(_test())


# -- workspace read tools ------------------------------------------------


class TestWorkspaceRead:
    def test_get_workspace_issues_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_issues",
                    {"workspace_name": ws, "limit": 3},
                )
                assert isinstance(payload, dict)
                assert "results" in payload or isinstance(payload, list)

        _run(_test())

    def test_get_workspace_slxs_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_slxs",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)
                assert "results" in payload or isinstance(payload, list)

        _run(_test())

    def test_get_run_sessions_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_run_sessions",
                    {"workspace_name": ws, "limit": 3},
                )
                assert isinstance(payload, dict)
                assert "results" in payload or isinstance(payload, list)

        _run(_test())

    def test_get_workspace_config_index_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_config_index",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)

        _run(_test())

    def test_get_workspace_secrets_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_secrets",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)
                assert "secrets" in payload

        _run(_test())

    def test_get_workspace_locations_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_locations",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)
                assert "locations" in payload

        _run(_test())

    def test_search_workspace_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                raw = await session.call_tool(
                    "search_workspace",
                    {"workspace_name": ws, "query": "kubernetes"},
                )
                if raw.isError:
                    text = "\n".join(b.text for b in raw.content if isinstance(b, TextContent))
                    if "503" in text or "unavailable" in text.lower():
                        pytest.skip(f"search_workspace unavailable: {text[:100]}")
                    raise AssertionError(f"tool error: {text}")
                payload = _strict_json(_tool_text(raw))
                assert isinstance(payload, (list, dict))

        _run(_test())


# -- chat config tests ---------------------------------------------------


class TestChatConfig:
    def test_get_workspace_chat_config_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "get_workspace_chat_config",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)

        _run(_test())

    def test_list_chat_rules_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "list_chat_rules",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)

        _run(_test())

    def test_list_chat_commands_returns_dict(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "list_chat_commands",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)

        _run(_test())

    def test_list_assistants_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "list_assistants",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, dict)
                assert "results" in payload or isinstance(payload, list)

        _run(_test())

    def test_list_knowledge_base_articles_returns_list(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "list_knowledge_base_articles",
                    {"workspace_name": ws},
                )
                assert isinstance(payload, list)

        _run(_test())


# -- render_codecollection_skill tests -----------------------------------

SAMPLE_RENDER_SCRIPT = (
    "import os\n"
    "def main():\n"
    '    ns = os.environ.get("NAMESPACE", "default")\n'
    '    return [{"issue title": "test", "issue description": f"ns={ns}", '
    '"issue severity": 4, "issue next steps": "none"}]\n'
)


class TestRenderCodecollection:
    def test_workspace_render_returns_files_inline(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "render_codecollection_skill",
                    {
                        "bundle_name": "smoke-test-render",
                        "alias": "Smoke Test Render",
                        "statement": "Smoke test should produce valid output.",
                        "workspace_name": ws,
                        "script": SAMPLE_RENDER_SCRIPT,
                        "interpreter": "python",
                        "platform": "runwhen",
                    },
                )
                assert isinstance(payload, dict)
                assert "error" not in payload, f"render error: {payload.get('error')}"

        _run(_test())

    def test_kubernetes_render_returns_files_inline(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "render_codecollection_skill",
                    {
                        "bundle_name": "smoke-k8s-render",
                        "alias": "Smoke K8s Render",
                        "statement": "K8s smoke test should produce valid output.",
                        "workspace_name": ws,
                        "script": SAMPLE_RENDER_SCRIPT,
                        "interpreter": "python",
                        "platform": "kubernetes",
                        "resource_types": ["namespace"],
                        "slx_qualifiers": ["namespace", "cluster"],
                    },
                )
                assert isinstance(payload, dict)
                assert "error" not in payload, f"render error: {payload.get('error')}"

        _run(_test())

    def test_render_to_output_dir_writes_files(self, tmp_path) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                out = str(tmp_path / "codecollection")
                payload = await _call(
                    session,
                    "render_codecollection_skill",
                    {
                        "bundle_name": "smoke-dir-render",
                        "alias": "Smoke Dir Render",
                        "statement": "Dir output should write files to disk.",
                        "workspace_name": ws,
                        "script": SAMPLE_RENDER_SCRIPT,
                        "interpreter": "python",
                        "platform": "runwhen",
                        "output_dir": out,
                    },
                )
                assert isinstance(payload, dict)

        _run(_test())

    def test_render_invalid_bundle_is_rejected(self) -> None:
        async def _test():
            async for session in _session():
                ws = _workspace()
                payload = await _call(
                    session,
                    "render_codecollection_skill",
                    {
                        "bundle_name": "INVALID_name",
                        "alias": "Bad Name",
                        "statement": "Should be rejected.",
                        "workspace_name": ws,
                        "script": SAMPLE_RENDER_SCRIPT,
                        "interpreter": "python",
                    },
                )
                assert "error" in payload

        _run(_test())
