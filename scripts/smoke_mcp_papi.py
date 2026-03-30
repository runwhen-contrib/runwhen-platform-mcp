#!/usr/bin/env python3
"""Live smoke: exercise MCP tool implementations against PAPI (no MCP client).

Uses the **same environment variables as the deployed MCP server**:
  RW_API_URL      — e.g. https://papi.beta.runwhen.com
  RUNWHEN_TOKEN   — JWT or PAT

Optional:
  RW_SMOKE_WORKSPACE — workspace short name (default: t-oncall)

Example:
  export RW_API_URL=... RUNWHEN_TOKEN=...
  python scripts/smoke_mcp_papi.py

Exit code 0 if all checks pass; 1 on failure; 2 if env is incomplete.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from typing import Any


def _strict_json(text: str) -> Any:
    def _reject(constant: str) -> Any:  # noqa: ANN401
        raise ValueError(f"non-standard JSON constant: {constant!r}")

    return json.loads(text, parse_constant=_reject)


def _check(label: str, raw: str) -> None:
    _strict_json(raw)
    print(f"OK  {label}")


async def _run_all() -> None:
    import runwhen_platform_mcp.server as srv

    importlib.reload(srv)

    ws = os.environ.get("RW_SMOKE_WORKSPACE", "t-oncall").strip()

    steps = [
        ("list_workspaces", srv.list_workspaces()),
        ("get_workspace_issues", srv.get_workspace_issues(ws, limit=5)),
        ("get_workspace_slxs", srv.get_workspace_slxs(ws)),
        ("get_run_sessions", srv.get_run_sessions(ws, limit=5)),
        ("get_workspace_config_index", srv.get_workspace_config_index(ws)),
        ("get_workspace_chat_config", srv.get_workspace_chat_config(ws)),
        (
            "list_chat_rules",
            srv.list_chat_rules(workspace_name=ws),
        ),
        (
            "list_chat_commands",
            srv.list_chat_commands(workspace_name=ws),
        ),
        ("search_workspace", srv.search_workspace("pod", ws)),
        ("list_knowledge_base_articles", srv.list_knowledge_base_articles(ws, limit=5)),
        ("get_workspace_secrets", srv.get_workspace_secrets(ws)),
        ("get_workspace_locations", srv.get_workspace_locations(ws)),
        ("get_workspace_context", srv.get_workspace_context(reload=False)),
        ("search_registry", srv.search_registry("kubernetes", max_results=3)),
    ]

    for label, coro in steps:
        raw = await coro
        _check(label, raw)

    script = "def main():\n    return []\n"
    raw = await srv.validate_script(script=script, interpreter="python", task_type="task")
    _check("validate_script", raw)

    slx_raw = await srv.get_workspace_slxs(ws)
    slx_payload = _strict_json(slx_raw)
    slxs = slx_payload if isinstance(slx_payload, list) else slx_payload.get("results", [])
    if slxs and isinstance(slxs[0], dict):
        first = slxs[0]
        slx_name = (
            first.get("shortName")
            or first.get("short_name")
            or first.get("name")
            or first.get("slxName")
        )
        if slx_name:
            rb = await srv.get_slx_runbook(slx_name, ws)
            _check("get_slx_runbook", rb)
        else:
            print("SKIP get_slx_runbook (no slx name in first row)")
    else:
        print("SKIP get_slx_runbook (no SLXs)")


def main() -> int:
    if not os.environ.get("RUNWHEN_TOKEN") or not os.environ.get("RW_API_URL"):
        print(
            "Missing RUNWHEN_TOKEN or RW_API_URL — use the same values as the MCP server.",
            file=sys.stderr,
        )
        return 2
    try:
        asyncio.run(_run_all())
    except Exception as exc:  # noqa: BLE001 — smoke script surfaces any failure
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
