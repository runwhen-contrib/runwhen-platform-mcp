"""Streamable HTTP MCP client smoke against a **deployed** RunWhen MCP server.

Uses the official ``mcp`` Python client (same wire protocol as Cursor). Validates
``initialize``, ``tools/list``, and ``tools/call`` for ``list_workspaces`` and
``get_workspace_issues`` (workspace ``t-oncall`` by default).

Environment (repository secrets in CI):

- ``RUNWHEN_MCP_URL`` — full MCP endpoint, e.g. ``https://mcp.<env>.runwhen.com/mcp``
  (no trailing slash; see README remote section).
- ``RUNWHEN_TOKEN`` — Bearer token (JWT or PAT), same as MCP client ``Authorization``.

Optional:

- ``RW_SMOKE_WORKSPACE`` — short name (default ``t-oncall``).

When URL or token is unset, tests skip so forks / repos without secrets stay green.
They also skip when the endpoint is unreachable or rejects the token (HTTP 401/403):
both are problems with the CI secrets, not with the code under test, and the skip
reason says which secret to fix.

Requires ``mcp>=2``: the streamable-HTTP transport yields ``(read, write)`` and
takes an ``httpx2`` client. The server itself still uses ``httpx`` — the two
packages coexist and share no exception hierarchy.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

pytestmark = [pytest.mark.integration, pytest.mark.remote_mcp]


def _require_remote_mcp_env() -> None:
    if not os.environ.get("RUNWHEN_MCP_URL") or not os.environ.get("RUNWHEN_TOKEN"):
        pytest.skip(
            "Set RUNWHEN_MCP_URL and RUNWHEN_TOKEN to run remote MCP HTTP smoke "
            "(configure as GitHub Actions secrets for CI)."
        )


def _mcp_url() -> str:
    return os.environ["RUNWHEN_MCP_URL"].strip().rstrip("/")


def _workspace() -> str:
    return os.environ.get("RW_SMOKE_WORKSPACE", "t-oncall").strip()


def _strict_json(text: str) -> Any:
    def _reject(constant: str) -> Any:  # noqa: ANN401
        raise ValueError(f"non-standard JSON constant: {constant!r}")

    return json.loads(text, parse_constant=_reject)


def _tool_text(result: CallToolResult) -> str:
    if result.is_error:
        parts: list[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        raise AssertionError("tool error: " + (" | ".join(parts) if parts else "(no text)"))
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
    assert texts, "expected at least one TextContent block in tool result"
    return "\n".join(texts)


async def _run_remote_smoke(auth_rejections: list[int]) -> None:
    url = _mcp_url()
    token = os.environ["RUNWHEN_TOKEN"]
    ws = _workspace()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
    }
    timeout = httpx2.Timeout(120.0, connect=30.0)

    # mcp >=2 reports any non-404 HTTP error as MCPError(-32603, "Server returned
    # an error response") and drops the status code, so record auth rejections here.
    async def _record_auth_rejection(response: httpx2.Response) -> None:
        if response.status_code in (401, 403):
            auth_rejections.append(response.status_code)

    async with (
        httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            event_hooks={"response": [_record_auth_rejection]},
        ) as http,
        # mcp >=2 yields (read, write); the session-id getter it used to yield
        # third is gone, and was never used here.
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.server_info is not None

        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        sample = sorted(names)[:15]
        assert "list_workspaces" in names, f"list_workspaces missing; sample={sample}"
        assert "get_workspace_issues" in names

        raw_list = await session.call_tool("list_workspaces", {})
        text_list = _tool_text(raw_list)
        workspaces = _strict_json(text_list)
        assert isinstance(workspaces, list)
        short_names = {w.get("name") for w in workspaces if isinstance(w, dict)}
        assert short_names, "no workspaces accessible with this token"
        if ws not in short_names:
            ws = sorted(short_names)[0]

        raw_issues = await session.call_tool(
            "get_workspace_issues",
            {"workspace_name": ws, "limit": 5},
        )
        text_issues = _tool_text(raw_issues)
        _strict_json(text_issues)


def _connect_error(exc: BaseException) -> httpx2.ConnectError | None:
    """Return a ConnectError from *exc* or its nested causes/groups.

    Must match the transport's client library: ``httpx2.ConnectError`` is a
    distinct class from ``httpx.ConnectError``, not a subclass, so checking the
    wrong one turns "endpoint unreachable" from a skip into a hard failure.
    """
    if isinstance(exc, httpx2.ConnectError):
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


def test_remote_streamable_mcp_smoke() -> None:
    _require_remote_mcp_env()
    auth_rejections: list[int] = []
    try:
        asyncio.run(_run_remote_smoke(auth_rejections))
    except BaseException as exc:
        connect_err = _connect_error(exc)
        if connect_err is not None:
            pytest.skip(f"Remote MCP endpoint unreachable from this runner: {connect_err}")
        if auth_rejections:
            pytest.skip(
                f"Remote MCP endpoint rejected RUNWHEN_TOKEN (HTTP {auth_rejections[0]}); "
                "rotate the RUNWHEN_TOKEN secret for RUNWHEN_MCP_URL."
            )
        raise
