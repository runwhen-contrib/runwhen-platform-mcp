"""Live PAPI smoke tests — same HTTP paths and tool logic as the MCP server.

These are **not** MCP transport tests (no JSON-RPC over streamable-http). They
validate that with RUNWHEN_TOKEN + RW_API_URL (the same credentials the remote
MCP server uses), the tool implementations return strict JSON and succeed.

Run with RUNWHEN_TOKEN and RW_API_URL set, then:
  pytest tests/test_papi_live_smoke.py -m integration -v

Optional:
  RW_SMOKE_WORKSPACE=t-oncall   (default: t-oncall)

In CI without secrets, tests are skipped.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from typing import Any

import pytest

pytestmark = pytest.mark.integration


def _strict_json(text: str) -> Any:
    """Reject NaN/Infinity tokens that break downstream JSON.parse (JS)."""

    def _reject(constant: str) -> Any:  # noqa: ANN401
        raise ValueError(f"non-standard JSON constant: {constant!r}")

    return json.loads(text, parse_constant=_reject)


def _require_live_env() -> None:
    if not os.environ.get("RUNWHEN_TOKEN") or not os.environ.get("RW_API_URL"):
        pytest.skip("Set RUNWHEN_TOKEN and RW_API_URL (same as MCP server env)")


@pytest.fixture(scope="module")
def live_server():
    _require_live_env()
    import runwhen_platform_mcp.server as srv

    importlib.reload(srv)
    return srv


def _workspace() -> str:
    return os.environ.get("RW_SMOKE_WORKSPACE", "t-oncall").strip()


def _run(coro):
    return asyncio.run(coro)


def test_list_workspaces_strict_json(live_server) -> None:
    raw = _run(live_server.list_workspaces())
    data = _strict_json(raw)
    assert isinstance(data, list)
    names = {w.get("name") for w in data if isinstance(w, dict)}
    sample = sorted(names)[:20]
    assert _workspace() in names, f"{_workspace()} not in accessible workspaces: {sample}"


@pytest.mark.parametrize(
    "name,coro_factory",
    [
        (
            "get_workspace_issues",
            lambda s, ws: s.get_workspace_issues(ws, limit=3),
        ),
        ("get_workspace_slxs", lambda s, ws: s.get_workspace_slxs(ws)),
        ("get_run_sessions", lambda s, ws: s.get_run_sessions(ws, limit=3)),
        ("get_workspace_config_index", lambda s, ws: s.get_workspace_config_index(ws)),
        ("get_workspace_chat_config", lambda s, ws: s.get_workspace_chat_config(ws)),
        (
            "list_chat_rules",
            lambda s, ws: s.list_chat_rules(scope_type="workspace", scope_id=ws),
        ),
        (
            "list_chat_commands",
            lambda s, ws: s.list_chat_commands(scope_type="workspace", scope_id=ws),
        ),
        ("get_workspace_secrets", lambda s, ws: s.get_workspace_secrets(ws)),
        ("get_workspace_locations", lambda s, ws: s.get_workspace_locations(ws)),
        (
            "list_knowledge_base_articles",
            lambda s, ws: s.list_knowledge_base_articles(ws, limit=5),
        ),
    ],
)
def test_workspace_tool_strict_json(live_server, name: str, coro_factory) -> None:
    ws = _workspace()
    raw = _run(coro_factory(live_server, ws))
    _strict_json(raw)


def test_search_workspace_strict_json(live_server) -> None:
    raw = _run(live_server.search_workspace("pod", _workspace()))
    _strict_json(raw)


def test_search_registry_strict_json(live_server) -> None:
    raw = _run(live_server.search_registry("kubernetes", max_results=2))
    data = _strict_json(raw)
    assert "results" in data


def test_get_workspace_context_strict_json(live_server) -> None:
    raw = _run(live_server.get_workspace_context(reload=False))
    _strict_json(raw)


def test_validate_script_strict_json(live_server) -> None:
    script = """def main():
    return []
"""
    raw = _run(live_server.validate_script(script, interpreter="python", task_type="task"))
    _strict_json(raw)


def test_get_slx_runbook_when_slx_exists(live_server) -> None:
    """If the workspace has SLXs, runbook fetch must return strict JSON."""
    ws = _workspace()
    raw_slxs = _run(live_server.get_workspace_slxs(ws))
    slx_payload = _strict_json(raw_slxs)
    slxs = slx_payload if isinstance(slx_payload, list) else slx_payload.get("results", [])
    if not slxs:
        pytest.skip("No SLXs in workspace — cannot test get_slx_runbook")
    first = slxs[0] if isinstance(slxs[0], dict) else {}
    slx_name = (
        first.get("shortName")
        or first.get("short_name")
        or first.get("name")
        or first.get("slxName")
    )
    if not slx_name:
        pytest.skip("Could not read SLX name from list response")
    raw = _run(live_server.get_slx_runbook(slx_name, ws))
    _strict_json(raw)
