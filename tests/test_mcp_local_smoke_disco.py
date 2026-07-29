"""MCP smoke tests for discovery tools — only run against a local server.

These tools may not be deployed yet. These tests require LOCAL_MCP_SMOKE=true
(plus RUNWHEN_MCP_URL + RUNWHEN_TOKEN) to avoid running against a remote
deployed server that lacks the tools. Set by the mcp-smoke CI job only.
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


def _require_env() -> None:
    if os.environ.get("LOCAL_MCP_SMOKE") != "true":
        pytest.skip(
            "LOCAL_MCP_SMOKE=true not set — this test requires a local "
            "MCP server running the current branch code."
        )
    if not os.environ.get("RUNWHEN_MCP_URL") or not os.environ.get("RUNWHEN_TOKEN"):
        pytest.skip("Set RUNWHEN_MCP_URL and RUNWHEN_TOKEN for local MCP discovery smoke.")


def _mcp_url() -> str:
    return os.environ["RUNWHEN_MCP_URL"].strip().rstrip("/")


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


async def _run_discovery_platforms() -> None:
    url = _mcp_url()
    token = os.environ["RUNWHEN_TOKEN"]

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
        await session.initialize()

        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert "list_discovery_platforms" in names, (
            f"'list_discovery_platforms' not on server; available (first 20): {sorted(names)[:20]}"
        )

        raw = await session.call_tool("list_discovery_platforms", {})
        text = _tool_text(raw)
        payload = _strict_json(text)
        assert isinstance(payload, dict)
        assert "platforms" in payload
        platforms = payload["platforms"]
        assert isinstance(platforms, list)
        assert len(platforms) >= 2
        pnames = {p["platform"] for p in platforms if isinstance(p, dict)}
        assert "kubernetes" in pnames


def test_list_discovery_platforms() -> None:
    _require_env()
    try:
        asyncio.run(_run_discovery_platforms())
    except BaseException as exc:
        connect_err = _connect_error(exc)
        if connect_err is not None:
            pytest.skip(f"MCP endpoint unreachable from this runner: {connect_err}")
        raise


async def _run_indexed_resource_types() -> None:
    url = _mcp_url()
    token = os.environ["RUNWHEN_TOKEN"]

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
        await session.initialize()

        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert "list_indexed_resource_types" in names, (
            f"'list_indexed_resource_types' not on server; "
            f"available (first 20): {sorted(names)[:20]}"
        )

        raw = await session.call_tool("list_indexed_resource_types", {"platform": "kubernetes"})
        text = _tool_text(raw)
        payload = _strict_json(text)
        assert isinstance(payload, dict)
        assert "resource_types" in payload
        assert isinstance(payload["resource_types"], list)
        assert "namespace" in payload["resource_types"]


def test_list_indexed_resource_types_kubernetes() -> None:
    _require_env()
    try:
        asyncio.run(_run_indexed_resource_types())
    except BaseException as exc:
        connect_err = _connect_error(exc)
        if connect_err is not None:
            pytest.skip(f"MCP endpoint unreachable from this runner: {connect_err}")
        raise
