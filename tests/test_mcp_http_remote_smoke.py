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
    assert texts, "expected at least one TextContent block in tool result"
    return "\n".join(texts)


async def _run_remote_smoke() -> None:
    url = _mcp_url()
    token = os.environ["RUNWHEN_TOKEN"]
    ws = _workspace()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
    }
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with (
        httpx.AsyncClient(headers=headers, timeout=timeout) as http,
        streamable_http_client(url, http_client=http) as (read, write, _get_sid),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.serverInfo is not None

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
        assert ws in short_names, (
            f"smoke workspace {ws!r} not in list_workspaces; got {sorted(short_names)[:30]}"
        )

        raw_issues = await session.call_tool(
            "get_workspace_issues",
            {"workspace_name": ws, "limit": 5},
        )
        text_issues = _tool_text(raw_issues)
        _strict_json(text_issues)


def test_remote_streamable_mcp_smoke() -> None:
    _require_remote_mcp_env()
    asyncio.run(_run_remote_smoke())
