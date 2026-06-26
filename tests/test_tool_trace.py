"""Tests for MCP tool-call tracing middleware."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.middleware.middleware import MiddlewareContext
from mcp.types import CallToolRequestParams

from runwhen_platform_mcp.tool_trace import (
    MCP_TOOL_HEADER,
    REQUEST_ID_HEADER,
    ToolTraceMiddleware,
    _current_mcp_tool,
    _current_request_id,
    trace_headers,
)


@pytest.fixture(autouse=True)
def _clear_trace_context() -> None:
    _current_mcp_tool.set(None)
    _current_request_id.set(None)


class TestTraceHeaders:
    def test_empty_when_no_active_tool(self) -> None:
        assert trace_headers() == {}

    def test_includes_tool_and_request_id(self) -> None:
        _current_mcp_tool.set("list_workspaces")
        _current_request_id.set("abc123")
        assert trace_headers() == {
            MCP_TOOL_HEADER: "list_workspaces",
            REQUEST_ID_HEADER: "abc123",
        }


class TestToolTraceMiddleware:
    def test_binds_context_and_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        async def _run() -> None:
            middleware = ToolTraceMiddleware()
            params = CallToolRequestParams(
                name="get_workspace_issues", arguments={"workspace_name": "oncall-test"}
            )
            context = MiddlewareContext(
                message=params, method="tools/call", type="request", source="client"
            )
            call_next = AsyncMock(return_value={"issues": []})

            with caplog.at_level(logging.INFO, logger="runwhen_platform_mcp.tool_trace"):
                result = await middleware.on_call_tool(context, call_next)

            assert result == {"issues": []}
            call_next.assert_awaited_once()
            assert _current_mcp_tool.get() is None
            assert _current_request_id.get() is None

            records = [
                json.loads(r.message)
                for r in caplog.records
                if r.name == "runwhen_platform_mcp.tool_trace"
            ]
            assert len(records) == 2
            assert records[0]["event"] == "mcp_tool_start"
            assert records[0]["mcp_tool"] == "get_workspace_issues"
            assert records[0]["workspace"] == "oncall-test"
            assert records[1]["event"] == "mcp_tool_completed"
            assert records[1]["status"] == "ok"
            assert records[1]["duration_ms"] >= 0

        asyncio.run(_run())

    def test_logs_error_status(self, caplog: pytest.LogCaptureFixture) -> None:
        async def _run() -> None:
            middleware = ToolTraceMiddleware()
            params = CallToolRequestParams(name="run_slx", arguments={})
            context = MiddlewareContext(
                message=params, method="tools/call", type="request", source="client"
            )

            async def _fail(_ctx: MiddlewareContext[CallToolRequestParams]) -> None:
                raise RuntimeError("boom")

            with (
                caplog.at_level(logging.INFO, logger="runwhen_platform_mcp.tool_trace"),
                pytest.raises(RuntimeError, match="boom"),
            ):
                await middleware.on_call_tool(context, _fail)

            records = [
                json.loads(r.message)
                for r in caplog.records
                if r.name == "runwhen_platform_mcp.tool_trace"
            ]
            completed = next(r for r in records if r["event"] == "mcp_tool_completed")
            assert completed["status"] == "error"
            assert completed["error"] == "boom"

        asyncio.run(_run())
