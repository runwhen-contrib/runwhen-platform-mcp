"""Tests for the capability-task MCP tools: cap_list and cap_run.

The platform API is mocked at the module's HTTP helpers. As in
``test_tool_handlers.py``, handlers are called directly with every argument
passed explicitly (FastMCP ``Field`` defaults do not resolve on a direct call).
"""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from runwhen_platform_mcp import server
from runwhen_platform_mcp.capability_tasks import (
    CAP_LIST_OUTPUT_SCHEMA,
    CAP_RUN_OUTPUT_SCHEMA,
    CapListResult,
    CapRunResult,
)

_CLUSTER = "kubernetes/clusters/prod"
_TASK_URL = "/api/v4/workspaces/ws/capabilities/k8s-discovery/tasks"
_SCHEMA = {
    "$id": "k8s-discovery/schemas/k8s_object.json",
    "version": "0.1.0",
    "title": "K8sObjectResult",
    "type": "object",
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _workspace():
    with mock.patch.object(server, "_resolve_workspace", mock.AsyncMock(return_value="ws")):
        yield


def _described(**overrides):
    body = {
        "capability": "k8s-discovery",
        "task": "inspect",
        "version": "0.1.0",
        "description": "Get, describe or tail one object",
        "readOnly": True,
        "invocation": ["queued"],
        "path": "/capabilities/k8s-discovery",
        "file": "/capabilities/k8s-discovery/capability.yaml",
        "inputSchema": {"type": "object", "properties": {"kind": {"type": "string"}}},
        "outputs": {
            "object": {
                "kind": "rw.k8s_object.v1",
                "schemaId": _SCHEMA["$id"],
                "summary": "K8sObjectResult {}",
                "schema": _SCHEMA,
            }
        },
        "example": None,
        "rendering": None,
    }
    body.update(overrides)
    return body


def _view(status="succeeded"):
    return {
        "runUuid": "run-1",
        "path": "/capabilities/k8s-discovery",
        "file": "/capabilities/k8s-discovery/capability.yaml",
        "status": status,
        "outputs": {"object": {"schema": {"$id": _SCHEMA["$id"], "version": "0.1.0"}, "value": {}}}
        if status == "succeeded"
        else {},
        "schemas": {_SCHEMA["$id"]: _SCHEMA} if status == "succeeded" else None,
        "error": None,
    }


def _cap_run(**kwargs):
    args = {
        "workspace_name": "ws",
        "operation": "run",
        "task": "k8s-discovery/inspect",
        "resource": _CLUSTER,
        "inputs": None,
        "run_id": "",
        "render": "",
        "wait_seconds": 60,
    }
    args.update(kwargs)
    return _run(server.cap_run(**args))


class TestCapList:
    def test_each_task_is_shaped_like_a_tool(self) -> None:
        listing = {
            "applicable": [
                {
                    "capability": "k8s-discovery",
                    "task": "inspect",
                    "description": "Get, describe or tail one object",
                    "readOnly": True,
                    "path": "/capabilities/k8s-discovery",
                    "file": "/capabilities/k8s-discovery/capability.yaml",
                    "inputSchema": {"type": "object", "properties": {"kind": {"type": "string"}}},
                    "outputs": [{"name": "object", "summary": "K8sObjectResult {found}"}],
                }
            ],
            "notApplicableCount": 2,
        }
        with mock.patch.object(server, "_papi_get", mock.AsyncMock(return_value=listing)) as get:
            result = _run(server.cap_list(workspace_name="ws", resource=_CLUSTER))
        get.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capabilities", params={"subject": _CLUSTER}
        )
        structured = result.structured_content
        CapListResult.model_validate(structured)
        task = structured["tasks"][0]
        assert task["name"] == "k8s-discovery/inspect"
        assert task["inputSchema"]["properties"] == {"kind": {"type": "string"}}
        assert task["outputs"] == {"object": "K8sObjectResult {found}"}
        assert task["file"] == "/capabilities/k8s-discovery/capability.yaml"
        assert structured["notApplicableCount"] == 2


class TestCapListSync:
    def test_a_sync_task_lists_the_command_request_it_takes(self) -> None:
        listing = {
            "applicable": [
                {
                    "capability": "k8s-discovery",
                    "task": "cli",
                    "readOnly": True,
                    "invocation": ["sync"],
                    "inputSchema": {"type": "object", "properties": {"argv": {}}},
                    "outputs": [],
                }
            ]
        }
        with mock.patch.object(server, "_papi_get", mock.AsyncMock(return_value=listing)):
            result = _run(server.cap_list(workspace_name="ws", resource=_CLUSTER))
        assert result.structured_content["tasks"][0]["inputSchema"]["required"] == ["command"]


class TestCapRunDescribe:
    def test_returns_the_task_schemas_and_passes_render(self) -> None:
        get = mock.AsyncMock(return_value=_described(rendering="# jq"))
        with mock.patch.object(server, "_papi_get", get):
            result = _cap_run(operation="describe", render="jq")
        get.assert_awaited_once_with(
            f"{_TASK_URL}/inspect",
            params={"example": "true", "subject": _CLUSTER, "render": "jq"},
        )
        structured = result.structured_content
        CapRunResult.model_validate(structured)
        assert structured["outputSchema"]["properties"]["object"] == _SCHEMA
        assert structured["inputSchema"]["properties"] == {"kind": {"type": "string"}}
        assert structured["rendering"] == "# jq"
        assert "status" not in structured

    def test_a_sync_task_takes_a_command(self) -> None:
        get = mock.AsyncMock(return_value=_described(task="cli", invocation=["sync"]))
        with mock.patch.object(server, "_papi_get", get):
            result = _cap_run(operation="describe", task="k8s-discovery/cli", resource="")
        assert result.structured_content["inputSchema"]["required"] == ["command"]


class TestCapRunQueued:
    def test_runs_and_waits_for_the_outputs(self) -> None:
        get = mock.AsyncMock(side_effect=[_described(), _view("running"), _view("succeeded")])
        post = mock.AsyncMock(return_value=(202, {"runUuid": "run-1", "status": "queued"}))
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
        ):
            result = _cap_run(inputs={"kind": "Deployment"})
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/capability-runs",
            {
                "capability": "k8s-discovery",
                "task": "inspect",
                "inputs": {"kind": "Deployment"},
                "target": {"path": _CLUSTER},
            },
        )
        assert get.await_args_list[1].kwargs["params"] == {"wait": 20, "includeSchemas": "true"}
        structured = result.structured_content
        CapRunResult.model_validate(structured)
        assert structured["status"] == "succeeded"
        assert structured["outputs"]["object"]["schema"]["$id"] == _SCHEMA["$id"]
        assert structured["schemas"][_SCHEMA["$id"]] == _SCHEMA

    def test_a_run_still_going_after_the_wait_can_be_resumed(self) -> None:
        get = mock.AsyncMock(side_effect=[_described(), _view("queued")])
        post = mock.AsyncMock(return_value=(202, {"runUuid": "run-1", "status": "queued"}))
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
        ):
            result = _cap_run(inputs={"kind": "Pod"}, wait_seconds=5)
        assert get.await_args_list[1].kwargs["params"]["wait"] == 5
        structured = result.structured_content
        assert structured["status"] == "queued"
        assert structured["runId"] == "run-1"
        assert "run_id=run-1" in structured["message"]

    def test_resuming_only_waits(self) -> None:
        get = mock.AsyncMock(return_value=_view("succeeded"))
        post = mock.AsyncMock()
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
        ):
            result = _cap_run(resource="", run_id="run-1")
        get.assert_awaited_once()
        assert get.await_args.args[0] == f"{_TASK_URL}/inspect/runs/run-1"
        post.assert_not_called()
        assert result.structured_content["status"] == "succeeded"

    def test_a_task_that_is_not_read_only_is_refused(self) -> None:
        get = mock.AsyncMock(return_value=_described(readOnly=False))
        post = mock.AsyncMock()
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
            pytest.raises(ValueError, match="not read-only"),
        ):
            _cap_run(inputs={})
        post.assert_not_called()

    def test_a_run_needs_a_resource(self) -> None:
        with pytest.raises(ValueError, match="resource is required"):
            _cap_run(resource="")

    def test_a_task_name_needs_a_capability(self) -> None:
        with pytest.raises(ValueError, match="capability/task"):
            _cap_run(task="inspect")


class TestCapRunSync:
    def test_runs_through_the_platform_cli_with_the_resource_as_target(self) -> None:
        described = _described(
            task="cli",
            invocation=["sync"],
            cli={"name": "kubectl"},
            outputs={
                "result": {
                    "schema": {
                        "$id": "k8s-discovery/schemas/cli_result.json",
                        "version": "0.1.0",
                    }
                }
            },
        )
        get = mock.AsyncMock(return_value=described)
        post = mock.AsyncMock(
            return_value=(
                200,
                {"argv": ["get", "pods"], "exitCode": 0, "stdout": "ok\n", "stderr": ""},
            )
        )
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
        ):
            result = _cap_run(task="k8s-discovery/cli", inputs={"command": "get pods"})
        post.assert_awaited_once_with(
            "/api/v4/workspaces/ws/platform-cli/kubectl:run",
            {"command": "get pods", "targetPath": _CLUSTER},
        )
        value = result.structured_content["outputs"]["result"]["value"]
        assert value["stdoutBytes"] == 3
        assert result.structured_content["outputs"]["result"]["schema"]["$id"].endswith(
            "cli_result.json"
        )

    def test_bad_command_inputs_never_reach_the_platform(self) -> None:
        get = mock.AsyncMock(
            return_value=_described(task="cli", invocation=["sync"], cli={"name": "kubectl"})
        )
        post = mock.AsyncMock()
        with (
            mock.patch.object(server, "_papi_get", get),
            mock.patch.object(server, "_papi_post", post),
            pytest.raises(ValueError, match="inputs.command"),
        ):
            _cap_run(task="k8s-discovery/cli", inputs={"cmd": "get pods"})
        post.assert_not_called()


class TestDeclaredSchemas:
    def test_both_tools_declare_an_object_output_schema(self) -> None:
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        for name, declared in (
            ("cap_list", CAP_LIST_OUTPUT_SCHEMA),
            ("cap_run", CAP_RUN_OUTPUT_SCHEMA),
        ):
            schema = tools[name].output_schema
            assert schema["type"] == "object"
            assert set(schema["properties"]) == set(declared["properties"])

    def test_http_mode_declares_the_same_output_schemas(self) -> None:
        # HTTP mode re-registers every tool with an auth check; the output
        # schemas must survive that re-registration. The server's own listing
        # filters on that check, so read what was registered.
        with (
            mock.patch("runwhen_platform_mcp.consent_ui.patch_fastmcp_consent_ui", lambda: None),
            mock.patch("runwhen_platform_mcp.auth.build_auth_provider", return_value=None),
        ):
            http_mcp = server._build_http_server()
        tools = {t.name: t for t in asyncio.run(http_mcp._local_provider.list_tools())}
        for name, declared in (
            ("cap_list", CAP_LIST_OUTPUT_SCHEMA),
            ("cap_run", CAP_RUN_OUTPUT_SCHEMA),
        ):
            schema = tools[name].output_schema
            assert schema is not None
            assert set(schema["properties"]) == set(declared["properties"])

    def test_the_description_spells_out_capability(self) -> None:
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        assert "capability task" in tools["cap_run"].description
        assert "capability tasks" in tools["cap_list"].description
