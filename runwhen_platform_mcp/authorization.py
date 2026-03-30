"""Workspace-level authorization for the remote hosted MCP server.

Enforces workspace access control by checking user roles before tool execution.
The role hierarchy is: ADMIN > READ_WRITE > READ_AND_RUN > READ_ONLY.

Read-only tools (list, get, search) require at least READ_ONLY.
Write tools (run_script, commit_slx, delete_slx) require at least READ_WRITE.

``_make_workspace_auth_check`` in ``server.py`` uses ``get_user_workspace_role``
and ``minimum_role_for_tool`` to enforce per-tool authorization in HTTP mode.
"""

from __future__ import annotations

from enum import IntEnum

import httpx

WRITE_TOOLS = frozenset(
    {
        "run_script",
        "run_script_and_wait",
        "run_slx",
        "commit_slx",
        "delete_slx",
        "deploy_registry_codebundle",
        "create_chat_rule",
        "update_chat_rule",
        "create_chat_command",
        "update_chat_command",
        "create_knowledge_base_article",
        "update_knowledge_base_article",
        "delete_knowledge_base_article",
    }
)


class WorkspaceRole(IntEnum):
    """Workspace permission levels, ordered by privilege."""

    READ_ONLY = 1
    READ_AND_RUN = 2
    READ_WRITE = 3
    ADMIN = 4

    @classmethod
    def from_string(cls, role: str) -> WorkspaceRole:
        """Parse a role string (case-insensitive, handles underscores and hyphens)."""
        normalized = role.upper().replace("-", "_")
        try:
            return cls[normalized]
        except KeyError:
            return cls.READ_ONLY


def minimum_role_for_tool(tool_name: str) -> WorkspaceRole:
    """Return the minimum workspace role required to execute a tool."""
    if tool_name in WRITE_TOOLS:
        return WorkspaceRole.READ_WRITE
    return WorkspaceRole.READ_ONLY


async def get_user_workspace_role(
    papi_url: str,
    token: str,
    workspace_name: str,
    timeout: float = 10.0,
) -> WorkspaceRole | None:
    """Fetch the user's role for a workspace from PAPI.

    Returns the WorkspaceRole or None if the user has no access.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{papi_url}/api/v3/workspaces/{workspace_name}/permissions",
                headers=headers,
            )
            if resp.status_code in (401, 403, 404):
                return None
            resp.raise_for_status()
            data = resp.json()
            role_str = data.get("role") or data.get("permission", "")
            if not role_str:
                return None
            return WorkspaceRole.from_string(role_str)
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException):
        return None
