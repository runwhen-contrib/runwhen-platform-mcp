"""MCP tool-call tracing: structured logs and downstream header propagation.

When a tool runs, this middleware binds the active tool name and request id to
contextvars so ``server._headers()`` can forward them to PAPI as
``X-RunWhen-MCP-Tool`` and ``X-Request-ID``.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import CallToolRequestParams

logger = logging.getLogger("runwhen_platform_mcp.tool_trace")

_current_mcp_tool: ContextVar[str | None] = ContextVar("_current_mcp_tool", default=None)
_current_request_id: ContextVar[str | None] = ContextVar("_current_request_id", default=None)


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


MCP_TOOL_TRACE = _env_truthy(os.environ.get("MCP_TOOL_TRACE", "true"))

MCP_TOOL_HEADER = "X-RunWhen-MCP-Tool"
REQUEST_ID_HEADER = "X-Request-ID"


def trace_headers() -> dict[str, str]:
    """Headers to attach to outbound PAPI calls during an active tool invocation."""
    headers: dict[str, str] = {}
    tool = _current_mcp_tool.get()
    if tool:
        headers[MCP_TOOL_HEADER] = tool
    request_id = _current_request_id.get()
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return headers


class ToolTraceMiddleware(Middleware):
    """Log each ``tools/call`` and bind trace context for downstream PAPI requests."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, Any],
    ) -> Any:
        params = context.message
        tool_name = params.name
        workspace = None
        if isinstance(params.arguments, dict):
            ws = params.arguments.get("workspace_name")
            if isinstance(ws, str) and ws:
                workspace = ws

        request_id = uuid.uuid4().hex[:12]
        tool_token = _current_mcp_tool.set(tool_name)
        req_token = _current_request_id.set(request_id)

        start = time.perf_counter()
        status = "ok"
        error_text = ""

        if MCP_TOOL_TRACE:
            payload: dict[str, Any] = {
                "event": "mcp_tool_start",
                "mcp_tool": tool_name,
                "request_id": request_id,
            }
            if workspace:
                payload["workspace"] = workspace
            logger.info(json.dumps(payload, separators=(",", ":")))

        try:
            return await call_next(context)
        except Exception as exc:
            status = "error"
            error_text = str(exc)
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            if MCP_TOOL_TRACE:
                payload = {
                    "event": "mcp_tool_completed",
                    "mcp_tool": tool_name,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                    "status": status,
                }
                if workspace:
                    payload["workspace"] = workspace
                if error_text:
                    payload["error"] = error_text
                log_fn = logger.error if status == "error" else logger.info
                log_fn(json.dumps(payload, separators=(",", ":")))

            _current_mcp_tool.reset(tool_token)
            _current_request_id.reset(req_token)
