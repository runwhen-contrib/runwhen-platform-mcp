"""Tests for the capability build tools: cap_ls, cap_read, cap_glob, cap_grep,
cap_write, cap_edit, cap_test, cap_diff and cap_submit.

The platform API is mocked at the module's HTTP helpers, as in
``test_capability_tasks.py``. Handlers are called directly with every argument
passed explicitly (FastMCP ``Field`` defaults do not resolve on a direct call).
"""

from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest

from runwhen_platform_mcp import server
from runwhen_platform_mcp.capability_fs_tools import (
    CAPABILITY_FS_READ_ONLY_TOOLS,
    CAPABILITY_FS_TOOLS,
)

_TOOL_NAMES = (
    "cap_ls",
    "cap_read",
    "cap_glob",
    "cap_grep",
    "cap_write",
    "cap_edit",
    "cap_test",
    "cap_diff",
    "cap_submit",
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _workspace():
    with mock.patch.object(server, "_resolve_workspace", mock.AsyncMock(return_value="ws")):
        yield


class TestManifest:
    def test_loads_exactly_nine_tools(self) -> None:
        assert set(CAPABILITY_FS_TOOLS) == set(_TOOL_NAMES)
        assert len(CAPABILITY_FS_TOOLS) == 9

    def test_every_tool_maps_to_a_capability_fs_route(self) -> None:
        for tool in CAPABILITY_FS_TOOLS.values():
            assert tool["path"].startswith("/api/v4/workspaces/{workspace}/capability-fs/")
            assert tool["method"] in ("GET", "POST")

    def test_read_only_tools_are_get(self) -> None:
        for name in CAPABILITY_FS_READ_ONLY_TOOLS:
            assert CAPABILITY_FS_TOOLS[name]["method"] == "GET"

    def test_write_tools_are_post(self) -> None:
        for name in _TOOL_NAMES:
            if name not in CAPABILITY_FS_READ_ONLY_TOOLS:
                assert CAPABILITY_FS_TOOLS[name]["method"] == "POST"


class TestRegistration:
    def test_exactly_nine_tools_registered_with_matching_names_and_descriptions(self) -> None:
        tools = {t.name: t for t in _run(server.mcp.list_tools())}
        for name in _TOOL_NAMES:
            assert tools[name].description == CAPABILITY_FS_TOOLS[name]["description"]

    def test_readonly_hint_matches_the_manifest_split(self) -> None:
        tools = {t.name: t for t in _run(server.mcp.list_tools())}
        for name in _TOOL_NAMES:
            expected = name in CAPABILITY_FS_READ_ONLY_TOOLS
            assert tools[name].annotations.read_only_hint is expected

    def test_http_mode_registers_the_same_descriptions_and_annotations(self) -> None:
        # HTTP mode re-registers every tool with an auth check, deriving
        # metadata straight from the function -- verify it carries the same
        # manifest-sourced description and readOnlyHint as stdio mode.
        with (
            mock.patch("runwhen_platform_mcp.consent_ui.patch_fastmcp_consent_ui", lambda: None),
            mock.patch("runwhen_platform_mcp.auth.build_auth_provider", return_value=None),
        ):
            http_mcp = server._build_http_server()
        tools = {t.name: t for t in _run(http_mcp._local_provider.list_tools())}
        for name in _TOOL_NAMES:
            assert tools[name].description == CAPABILITY_FS_TOOLS[name]["description"]
            expected = name in CAPABILITY_FS_READ_ONLY_TOOLS
            assert tools[name].annotations.read_only_hint is expected

    def test_write_tools_require_read_write_role(self) -> None:
        from runwhen_platform_mcp.authorization import WorkspaceRole, minimum_role_for_tool

        for name in ("cap_write", "cap_edit", "cap_test", "cap_submit"):
            assert minimum_role_for_tool(name) == WorkspaceRole.READ_WRITE
        for name in CAPABILITY_FS_READ_ONLY_TOOLS:
            assert minimum_role_for_tool(name) == WorkspaceRole.READ_ONLY


class TestAgentHeader:
    def test_every_papi_request_carries_the_agent_marker(self) -> None:
        with mock.patch.object(server, "_get_token", return_value="tok"):
            assert server._headers()["X-RunWhen-Agent"] == "mcp"


class TestGetTools:
    def test_cap_ls_sends_path_and_reason_as_query_params(self) -> None:
        get = mock.AsyncMock(return_value={"entries": []})
        with mock.patch.object(server, "_papi_get", get):
            result = _run(
                server.cap_ls(workspace_name="ws", reason="browse the tree", path="/capabilities")
            )
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/ls",
            params={"path": "/capabilities", "reason": "browse the tree"},
        )
        assert json.loads(result) == {"entries": []}

    def test_cap_ls_drops_the_unset_optional_path(self) -> None:
        get = mock.AsyncMock(return_value={"entries": []})
        with mock.patch.object(server, "_papi_get", get):
            _run(server.cap_ls(workspace_name="ws", reason="browse the tree"))
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/ls", params={"reason": "browse the tree"}
        )

    def test_cap_read_sends_the_line_range(self) -> None:
        get = mock.AsyncMock(return_value={"content": "..."})
        with mock.patch.object(server, "_papi_get", get):
            _run(
                server.cap_read(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health/capability.yaml",
                    reason="read before editing",
                    start_line=1,
                    end_line=20,
                )
            )
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/read",
            params={
                "path": "/capabilities/pgbouncer-health/capability.yaml",
                "start_line": 1,
                "end_line": 20,
                "reason": "read before editing",
            },
        )

    def test_cap_glob_sends_the_pattern(self) -> None:
        get = mock.AsyncMock(return_value={"paths": []})
        with mock.patch.object(server, "_papi_get", get):
            _run(
                server.cap_glob(
                    workspace_name="ws", pattern="/capabilities/**/capability.yaml", reason="scan"
                )
            )
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/glob",
            params={"pattern": "/capabilities/**/capability.yaml", "reason": "scan"},
        )

    def test_cap_grep_sends_pattern_path_and_ignore_case(self) -> None:
        get = mock.AsyncMock(return_value={"hits": []})
        with mock.patch.object(server, "_papi_get", get):
            _run(
                server.cap_grep(
                    workspace_name="ws",
                    pattern="pgbouncer",
                    reason="look for prior art",
                    path="/capabilities/pgbouncer-health",
                    ignore_case=True,
                )
            )
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/grep",
            params={
                "pattern": "pgbouncer",
                "path": "/capabilities/pgbouncer-health",
                "ignore_case": True,
                "reason": "look for prior art",
            },
        )

    def test_cap_diff_sends_no_path_when_omitted(self) -> None:
        get = mock.AsyncMock(return_value={"drafts": []})
        with mock.patch.object(server, "_papi_get", get):
            _run(server.cap_diff(workspace_name="ws", reason="check open drafts"))
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/diff", params={"reason": "check open drafts"}
        )


class TestPostTools:
    def test_cap_write_sends_a_json_body(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"path": "x", "valid": True}))
        with mock.patch.object(server, "_papi_post", post):
            result = _run(
                server.cap_write(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health/capability.yaml",
                    reason="create the capability",
                    content="name: pgbouncer-health",
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/write",
            {
                "path": "/capabilities/pgbouncer-health/capability.yaml",
                "content": "name: pgbouncer-health",
                "reason": "create the capability",
            },
        )
        assert json.loads(result) == {"path": "x", "valid": True}

    def test_cap_write_delete_omits_content(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"path": "x"}))
        with mock.patch.object(server, "_papi_post", post):
            _run(
                server.cap_write(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health/tasks/old.py",
                    reason="remove the old task",
                    delete=True,
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/write",
            {
                "path": "/capabilities/pgbouncer-health/tasks/old.py",
                "delete": True,
                "reason": "remove the old task",
            },
        )

    def test_cap_edit_sends_old_and_new_string(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"path": "x", "valid": True}))
        with mock.patch.object(server, "_papi_post", post):
            _run(
                server.cap_edit(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health/capability.yaml",
                    old_string="30m",
                    new_string="1h",
                    reason="widen the window",
                    replace_all=True,
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/edit",
            {
                "path": "/capabilities/pgbouncer-health/capability.yaml",
                "old_string": "30m",
                "new_string": "1h",
                "replace_all": True,
                "reason": "widen the window",
            },
        )

    def test_cap_test_sends_the_run_request(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"run_id": "r1", "status": "queued"}))
        with mock.patch.object(server, "_papi_post", post):
            _run(
                server.cap_test(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health",
                    reason="verify pool-errors",
                    task="pool-errors",
                    target="pgbouncer-prod",
                    inputs={"since": "30m"},
                    wait_seconds=30,
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/test",
            {
                "path": "/capabilities/pgbouncer-health",
                "task": "pool-errors",
                "target": "pgbouncer-prod",
                "inputs": {"since": "30m"},
                "wait_seconds": 30,
                "reason": "verify pool-errors",
            },
        )

    def test_cap_test_can_just_poll_a_run_id(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"run_id": "r1", "status": "succeeded"}))
        with mock.patch.object(server, "_papi_post", post):
            _run(
                server.cap_test(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health",
                    reason="keep waiting",
                    run_id="r1",
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/test",
            {"path": "/capabilities/pgbouncer-health", "run_id": "r1", "reason": "keep waiting"},
        )

    def test_cap_submit_sends_path_and_reason(self) -> None:
        post = mock.AsyncMock(return_value=(200, {"kind": "custom-capability-proposal"}))
        with mock.patch.object(server, "_papi_post", post):
            result = _run(
                server.cap_submit(
                    workspace_name="ws",
                    path="/capabilities/pgbouncer-health",
                    reason="pool errors are now counted",
                )
            )
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-fs/submit",
            {"path": "/capabilities/pgbouncer-health", "reason": "pool errors are now counted"},
        )
        assert json.loads(result) == {"kind": "custom-capability-proposal"}


class TestErrors:
    def test_a_papi_error_comes_back_as_a_json_error_object(self) -> None:
        get = mock.AsyncMock(side_effect=ValueError("PAPI returned 409 for /ls: ..."))
        with mock.patch.object(server, "_papi_get", get):
            result = _run(server.cap_ls(workspace_name="ws", reason="browse"))
        assert json.loads(result) == {"error": "PAPI returned 409 for /ls: ..."}
