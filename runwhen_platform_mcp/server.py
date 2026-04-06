"""RunWhen Platform MCP Server.

Exposes RunWhen workspace capabilities to MCP clients (Cursor, Claude Desktop, etc.)
by proxying to the RunWhen API and Agent services.

Supports two transport modes:
  - **stdio** (default): Local subprocess spawned by the MCP client. Auth via
    RUNWHEN_TOKEN environment variable.
  - **http** (remote): Streamable HTTP server for remote/hosted deployments.
    Auth via Bearer token (PAT or JWT) validated against PAPI, with optional
    Auth0 OAuth 2.1 for interactive clients.

Set MCP_TRANSPORT=http to run in remote mode.

The key tool is `workspace_chat` which passes through to the RunWhen Agent's
chat endpoint, giving MCP clients access to ~25+ internal tools (issue search,
task search, resource search, knowledge base, graphing, etc.) without needing
to re-implement any of them.

The Tool Builder tools (`run_script`, `get_run_status`, `get_run_output`,
`commit_slx`) replicate the platform's "Create Task" / Tool Builder flow,
allowing agents to write scripts locally, test them against live infrastructure,
and commit them as SLXs to a workspace.

Auth flow (stdio mode):
  1. User provides a RunWhen API token via RUNWHEN_TOKEN env var
  2. That same token is used for both API and Agent requests
  3. The Agent service validates the token by calling back to the API

Auth flow (http mode):
  1. MCP client authenticates via Bearer token (PAT or OAuth access token)
  2. Token is validated against PAPI /api/v3/users/whoami
  3. Per-request token is forwarded to PAPI and AgentFarm
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from contextvars import ContextVar
from typing import Annotated, Any
from urllib.parse import quote, urlencode

import httpx
import yaml
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field

load_dotenv()

PAPI_URL = os.environ.get("RW_API_URL", "").rstrip("/")
RUNWHEN_TOKEN = os.environ.get("RUNWHEN_TOKEN", "")
DEFAULT_WORKSPACE = os.environ.get("DEFAULT_WORKSPACE", "")
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio").lower()
MCP_SERVER_LABEL = os.environ.get("MCP_SERVER_LABEL", "")
REGISTRY_URL = os.environ.get("RUNWHEN_REGISTRY_URL", "https://registry.runwhen.com").rstrip("/")
# Browser UI base (e.g. https://app.test.runwhen.com). If unset, derived from RW_API_URL
# by swapping ``papi`` → ``app`` (same pattern as AgentFarm). Required for correct
# ``chatUrl`` when RW_API_URL is an internal cluster URL.
RUNWHEN_APP_URL = os.environ.get("RUNWHEN_APP_URL", "").strip().rstrip("/")

# Per-request token for HTTP mode. Set by auth middleware; falls back to
# RUNWHEN_TOKEN in stdio mode.
_request_token: ContextVar[str | None] = ContextVar("_request_token", default=None)


def _derive_agentfarm_url(api_url: str) -> str:
    """Derive the AgentFarm URL by swapping the ``papi`` subdomain for ``agentfarm``."""
    return re.sub(r"://papi\.", "://agentfarm.", api_url) if api_url else ""


def _derive_runwhen_app_url_from_papi(api_url: str) -> str:
    """Derive the RunWhen web app base URL (``papi`` → ``app``), e.g. hosted test/prod."""
    return re.sub(r"://papi\.", "://app.", api_url).rstrip("/") if api_url else ""


def _runwhen_app_base_url() -> str:
    """Public browser base for workspace chat links (trailing slash stripped)."""
    if RUNWHEN_APP_URL:
        return RUNWHEN_APP_URL
    return _derive_runwhen_app_url_from_papi(PAPI_URL)


def _format_workspace_chat_browser_url(app_base: str, workspace: str, session_id: str) -> str:
    """Build ``/workspace/{ws}/workspace-chat?session=...`` under the app origin."""
    base = app_base.rstrip("/")
    wseg = quote(workspace, safe="")
    q = urlencode({"session": session_id})
    return f"{base}/workspace/{wseg}/workspace-chat?{q}"


AGENTFARM_URL = _derive_agentfarm_url(PAPI_URL)
RUNWHEN_CONTEXT_FILE = os.environ.get("RUNWHEN_CONTEXT_FILE", "")


def _derive_env_label() -> str:
    """Derive a short environment label from ``RW_API_URL``.

    Examples: ``https://papi.beta.runwhen.com`` → ``beta``,
              ``https://papi.app.runwhen.com`` → ``app``.
    """
    if not PAPI_URL:
        return ""
    m = re.search(r"://papi\.(\w+)\.", PAPI_URL)
    return m.group(1) if m else ""


def _build_server_name() -> str:
    """Build a unique, human-readable MCP server name.

    When ``MCP_SERVER_LABEL`` is set, use it directly.  Otherwise combine
    the environment label and default workspace so that each MCP server
    instance is distinguishable when multiple environments are registered
    in the same MCP client.
    """
    if MCP_SERVER_LABEL:
        return f"RunWhen Platform ({MCP_SERVER_LABEL})"
    parts: list[str] = []
    env = _derive_env_label()
    if env:
        parts.append(env)
    if DEFAULT_WORKSPACE:
        parts.append(DEFAULT_WORKSPACE)
    if parts:
        return f"RunWhen Platform ({'/'.join(parts)})"
    return "RunWhen Platform"


def _build_server_instructions() -> str:
    """Build the MCP server instructions with environment identity.

    Including the target environment and workspace in the instructions helps
    LLM agents route tool calls to the correct server when multiple RunWhen
    MCP servers are registered (e.g. beta vs prod, different workspaces).
    """
    env = _derive_env_label()
    identity_parts: list[str] = []
    if env:
        identity_parts.append(f"environment={env}")
    if DEFAULT_WORKSPACE:
        identity_parts.append(f"workspace={DEFAULT_WORKSPACE}")
    if PAPI_URL:
        identity_parts.append(f"api={PAPI_URL}")

    identity = (
        f"Server identity: {', '.join(identity_parts)}.\n"
        "When multiple RunWhen MCP servers are configured, use THIS server's "
        "tools for operations targeting the environment and workspace above.\n\n"
        if identity_parts
        else ""
    )

    return (
        f"{identity}"
        "RunWhen Platform MCP server — workspace intelligence, task authoring, "
        "and infrastructure automation.\n\n"
        "IMPORTANT: Most tools require a `workspace_name` parameter. "
        "ALWAYS provide it — do NOT omit it or pass null. "
        "Use `list_workspaces` first if you don't know the workspace name.\n\n"
        #
        # ── Tool routing ──
        #
        "TOOL ROUTING — when to use `workspace_chat` vs direct tools:\n\n"
        "  PREFER `workspace_chat` for:\n"
        "  - Questions about specific topics (e.g. 'issues related to neo4j')\n"
        "  - Investigations that need keyword/semantic search across issues, "
        "resources, SLXs, or run sessions\n"
        "  - Multi-step analysis or correlation "
        "(e.g. 'what's wrong in the watcher namespace?')\n"
        "  - Any question where a knowledgeable human would need to search, "
        "filter, and interpret results\n"
        "  `workspace_chat` has internal tools (semantic search, keyword grep, "
        "resource graph traversal) that produce materially better answers "
        "than combining multiple direct API calls.\n"
        "  Responses include a `chatUrl` for the user to continue in the "
        "RunWhen UI.\n\n"
        "  PREFER direct tools for:\n"
        "  - `run_slx` — EXECUTE a task (workspace_chat CANNOT run tasks)\n"
        "  - Task authoring — `validate_script`, `run_script_and_wait`, "
        "`commit_slx`, `delete_slx`\n"
        "  - Registry — `search_registry`, `get_registry_codebundle`, "
        "`deploy_registry_codebundle`\n"
        "  - Chat configuration — `list_chat_rules`, `create_chat_rule`, etc.\n"
        "  - KB mutations — `create_knowledge_base_article`, "
        "`update_knowledge_base_article`, `delete_knowledge_base_article`\n"
        "  - Workspace discovery — `list_workspaces`\n"
        "  - Runner config — `get_workspace_secrets`, `get_workspace_locations`\n"
        "  - Local context — `get_workspace_context` (reads RUNWHEN.md)\n\n"
        "  The remaining read/query tools (`get_workspace_issues`, "
        "`get_workspace_slxs`, `get_run_sessions`, `get_issue_details`, "
        "`get_slx_runbook`, `get_workspace_config_index`, `search_workspace`, "
        "`list_knowledge_base_articles`) overlap with what `workspace_chat` "
        "can do internally. Use them ONLY when you need raw structured JSON "
        "for programmatic processing (e.g. counting, filtering by field, "
        "feeding into code). For user-facing answers, prefer `workspace_chat`."
        "\n\n"
        #
        # ── Registry / authoring ──
        #
        "REGISTRY (search before build): `search_registry` — find reusable "
        "automation; `get_registry_codebundle` — full details; "
        "`deploy_registry_codebundle` — deploy a registry codebundle as an SLX "
        "(different from `commit_slx` which embeds inline scripts).\n\n"
        "TASK AUTHORING WORKFLOW:\n"
        "0. `search_registry` — check for existing codebundles first\n"
        "1. `get_workspace_context` — load RUNWHEN.md rules (ALWAYS call first)\n"
        "2. `get_workspace_secrets` + `get_workspace_locations` — discover config\n"
        "3. `validate_script` — check contract compliance\n"
        "4. `run_script_and_wait` — test against live infrastructure\n"
        "5. `commit_slx` — save as SLX (supports task + SLI together)\n\n"
        "SCRIPT CONTRACT:\n"
        "- Python task: `main()` returns List[Dict] with keys: "
        "'issue title', 'issue description', 'issue severity' (1-4), "
        "'issue next steps'\n"
        "- Bash task: `main()` writes issue JSON array to FD 3 (>&3)\n"
        "- Python/Bash SLI: `main()` returns/writes float 0-1\n\n"
        "REQUIRED TAGS for `commit_slx`: "
        "access='read-write'|'read-only', "
        "data='logs-bulk'|'config'|'logs-stacktrace'"
    )


mcp = FastMCP(
    _build_server_name(),
    instructions=_build_server_instructions(),
)

# ---------------------------------------------------------------------------
# Tool Builder constants
# ---------------------------------------------------------------------------

RB_CODE_BUNDLE = {
    "repoUrl": "https://github.com/runwhen-contrib/rw-generic-codecollection.git",
    "ref": "main",
    "pathToRobot": "codebundles/tool-builder/runbook.robot",
}

SLI_CODE_BUNDLE = {
    "repoUrl": "https://github.com/runwhen-contrib/rw-generic-codecollection.git",
    "ref": "main",
    "pathToRobot": "codebundles/tool-builder/sli.robot",
}

CRON_SLI_CODE_BUNDLE = {
    "repoUrl": "https://github.com/runwhen-contrib/rw-workspace-utils.git",
    "ref": "main",
    "pathToRobot": "codebundles/cron-scheduler-sli/sli.robot",
}

POLL_INTERVAL_S = 5
MAX_POLL_DURATION_S = 300
ARTIFACT_SETTLE_DELAY_S = 2

GENERIC_SLX_ICON = (
    "https://storage.googleapis.com/runwhen-nonprod-shared-images/icons/prompt_suggestion.svg"
)


async def _fetch_artifact_content(signed_url: str) -> str | None:
    """Download artifact content from a signed GCS URL."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(signed_url)
            resp.raise_for_status()
            return resp.text
    except Exception:
        return None


async def _fetch_and_parse_artifacts(output_data: dict) -> dict:
    """Fetch artifact contents from signed URLs and parse into clean structures.

    Returns a dict with:
      - issues: list of issue dicts (title, severity, details, nextSteps, etc.)
      - stdout: concatenated stdout from the script
      - stderr: concatenated stderr from the script
      - report: full report entries (all non-issue obj strings from the log)
      - status: run status from the output data
    """
    result: dict[str, Any] = {
        "issues": [],
        "stdout": [],
        "stderr": [],
        "report": [],
    }

    for artifact in output_data.get("artifacts", []):
        signed_url = artifact.get("signedUrl")
        atype = artifact.get("type", "")
        if not signed_url or atype not in ("log", "issues"):
            continue

        content = await _fetch_artifact_content(signed_url)
        if not content:
            continue

        artifact["content"] = content

        if atype == "issues":
            for line in content.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    issue = json.loads(line)
                    if not issue or not isinstance(issue, dict):
                        continue
                    if not issue.get("title") and len(issue) == 0:
                        continue
                    result["issues"].append(
                        {
                            k: v
                            for k, v in issue.items()
                            if k
                            in (
                                "title",
                                "severity",
                                "details",
                                "nextSteps",
                                "expected",
                                "actual",
                                "reproduceHint",
                                "taskName",
                                "observedAt",
                            )
                        }
                    )
                except json.JSONDecodeError:
                    pass

        elif atype == "log":
            for line in content.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    obj = entry.get("obj", "")
                    if isinstance(obj, str):
                        if obj.startswith("Command stdout: "):
                            result["stdout"].append(obj[len("Command stdout: ") :])
                        elif obj.startswith("Command stderr: "):
                            result["stderr"].append(obj[len("Command stderr: ") :])
                        else:
                            result["report"].append(obj)
                    elif isinstance(obj, dict):
                        fmt = entry.get("fmt", "")
                        if fmt != "issue":
                            result["report"].append(json.dumps(obj))
                except json.JSONDecodeError:
                    pass

    result["stdout"] = "\n".join(result["stdout"])
    result["stderr"] = "\n".join(result["stderr"])
    result["report"] = "\n".join(result["report"])
    return result


def _get_token() -> str:
    """Get the current auth token.

    In HTTP mode, returns the per-request token set by the auth middleware.
    In stdio mode, returns the global RUNWHEN_TOKEN from the environment.
    Raises ValueError with a helpful message if no token is available.
    """
    token = _request_token.get()
    if token:
        return token

    if not RUNWHEN_TOKEN:
        if MCP_TRANSPORT == "http":
            raise ValueError(
                "No authentication token found. The remote MCP server requires "
                "a Bearer token (PAT or JWT) in the Authorization header."
            )
        raise ValueError(
            "RUNWHEN_TOKEN is not set. Provide a PAPI JWT token via environment variable. "
            "Get one from: POST /api/v3/token/ (email+password), "
            "GET /api/v3/users/get-token (browser session), "
            "or create a Personal Access Token under Profile → Personal Tokens in the RunWhen UI."
        )
    return RUNWHEN_TOKEN


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature validation. Returns the raw claims dict."""
    try:
        payload_b64 = token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


class _TTLCache:
    """Simple TTL + max-size cache to avoid unbounded growth in long-running HTTP servers."""

    def __init__(self, ttl_seconds: float = 300.0, max_size: int = 256) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max_size and key not in self._store:
            oldest_key = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic(), value)


_user_email_cache = _TTLCache(ttl_seconds=3600, max_size=256)


async def _get_user_email(token: str | None = None) -> str:
    """Resolve the authenticated user's email address.

    Uses the PAPI ``/api/v3/users/whoami`` endpoint (preferred), falling back
    to ``/api/v3/users/{id}/`` if whoami is unavailable.  Results are cached
    with a 1-hour TTL.

    Note: the whoami endpoint requires NO trailing slash on this PAPI instance.
    """
    token = token or _get_token()

    cached = _user_email_cache.get(token)
    if cached is not None:
        return cached

    payload = _decode_jwt_payload(token)

    for claim in ("email", "primary_email"):
        val = payload.get(claim)
        if val and isinstance(val, str) and "@" in val:
            _user_email_cache.set(token, val)
            return val

    # Preferred: whoami endpoint returns the current user from the JWT
    for path in ("/api/v3/users/whoami",):
        try:
            data = await _papi_get(path)
            email = data.get("primaryEmail") or data.get("primary_email")
            if email:
                _user_email_cache.set(token, email)
                return email
            username = data.get("username", "")
            if username and "@" in username:
                _user_email_cache.set(token, username)
                return username
        except Exception:
            continue

    # Fallback: fetch user by ID from JWT claims
    user_id = payload.get("user_id") or payload.get("sub")
    if user_id is not None:
        try:
            data = await _papi_get(f"/api/v3/users/{user_id}")
            email = data.get("primaryEmail") or data.get("primary_email")
            if email:
                _user_email_cache.set(token, email)
                return email
        except Exception:
            pass

    fallback = str(user_id) if user_id else "cursor@runwhen.com"
    _user_email_cache.set(token, fallback)
    return fallback


_workspace_cache = _TTLCache(ttl_seconds=300, max_size=128)


async def _fetch_workspace_list() -> list[dict[str, str]]:
    """Fetch and cache the list of accessible workspaces.

    Returns a list of {"name": short_name, "displayName": display_name} dicts.
    Cache is keyed by a SHA-256 of the token so concurrent HTTP users cannot
    collide. Entries expire after 5 minutes.
    """
    token = _get_token()
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = _workspace_cache.get(cache_key)
    if cached is not None:
        return cached

    data = await _papi_get("/api/v3/workspaces")
    workspaces = data if isinstance(data, list) else data.get("results", data)
    result = []
    for ws in workspaces:
        name = ws.get("name") or ws.get("shortName") or ws.get("short_name", "")
        display = ws.get("displayName") or ws.get("display_name") or name
        result.append({"name": name, "displayName": display})
    _workspace_cache.set(cache_key, result)
    return result


def _match_workspace(query: str, workspaces: list[dict[str, str]]) -> str | None:
    """Resolve a workspace query to its short name.

    Matches (in order): exact short name, case-insensitive short name,
    case-insensitive display name.
    """
    q = query.strip()
    for ws in workspaces:
        if ws["name"] == q:
            return ws["name"]
    q_lower = q.lower()
    for ws in workspaces:
        if ws["name"].lower() == q_lower:
            return ws["name"]
    for ws in workspaces:
        if ws["displayName"].lower() == q_lower:
            return ws["name"]
    return None


async def _resolve_workspace(workspace_name: str | None) -> str:
    """Resolve a workspace name (short name or display name) to the PAPI short name.

    When workspace_name is omitted and DEFAULT_WORKSPACE is unset, or when the
    provided name doesn't match any accessible workspace, returns a structured
    error with the list of available workspaces so the agent can ask the user.
    """
    ws = (workspace_name or "").strip() or DEFAULT_WORKSPACE
    if not ws:
        workspaces = await _fetch_workspace_list()
        names = [f"  - {w['name']} ({w['displayName']})" for w in workspaces]
        raise ValueError(
            "workspace_name is required. Available workspaces:\n"
            + "\n".join(names)
            + "\n\nAsk the user which workspace to use."
        )

    workspaces = await _fetch_workspace_list()
    resolved = _match_workspace(ws, workspaces)
    if resolved:
        return resolved

    names = [f"  - {w['name']} ({w['displayName']})" for w in workspaces]
    raise ValueError(
        f"Workspace '{ws}' not found. Available workspaces:\n"
        + "\n".join(names)
        + "\n\nThe user may have used a display name or alias. "
        "Ask them to clarify which workspace they mean."
    )


def _normalize_path(path: str) -> str:
    """Normalize an API path to have no trailing slash.

    All call-sites use the slash-free form. The HTTP helpers handle
    Django's APPEND_SLASH 301 redirects by retrying with a trailing
    slash when needed (preserving the HTTP method).
    """
    return path.rstrip("/")


def _is_slash_redirect(resp: httpx.Response) -> bool:
    """Return True if the response is a redirect that just adds a trailing slash.

    Django's APPEND_SLASH returns 301 to the same path with ``/`` appended.
    Following a 301/302 causes httpx to downgrade POST/DELETE to GET, which
    breaks mutating requests. We detect this pattern and retry with the
    correct path instead.
    """
    if resp.status_code not in (301, 302, 307, 308):
        return False
    location = resp.headers.get("location", "")
    return location.rstrip("/").endswith(resp.request.url.path.rstrip("/"))


async def _papi_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Make an authenticated GET request to PAPI."""
    path = _normalize_path(path)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            params=params,
        )
        _raise_for_papi_status(resp, path)
        return _safe_json_parse(resp, f"PAPI GET {path}")


async def _papi_post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """Make an authenticated POST request to PAPI. Returns (status_code, json).

    Handles Django APPEND_SLASH redirects by retrying with a trailing
    slash, preserving the POST method and body.
    """
    path = _normalize_path(path)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.post(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            json=body,
        )
        if _is_slash_redirect(resp):
            resp = await client.post(
                f"{PAPI_URL}{path}/",
                headers=_headers(),
                json=body,
            )
        _raise_for_papi_status(resp, path)
        return resp.status_code, _safe_json_parse(resp, f"PAPI POST {path}")


async def _papi_delete(
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Make an authenticated DELETE request to PAPI. Returns (status_code, json|text).

    Handles Django APPEND_SLASH redirects by retrying with a trailing
    slash, preserving the DELETE method and body.
    """
    path = _normalize_path(path)
    kwargs: dict[str, Any] = {"headers": _headers()}
    if body is not None:
        kwargs["json"] = body
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.delete(f"{PAPI_URL}{path}", **kwargs)
        if _is_slash_redirect(resp):
            resp = await client.delete(f"{PAPI_URL}{path}/", **kwargs)
        _raise_for_papi_status(resp, path)
        if resp.status_code == 204:
            return resp.status_code, {}
        try:
            return resp.status_code, _safe_json_parse(resp, f"PAPI DELETE {path}")
        except ValueError:
            return resp.status_code, {"message": resp.text[:500]}


async def _papi_patch(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """Make an authenticated PATCH request to PAPI. Returns (status_code, json)."""
    path = _normalize_path(path)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.patch(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            json=body,
        )
        if _is_slash_redirect(resp):
            resp = await client.patch(
                f"{PAPI_URL}{path}/",
                headers=_headers(),
                json=body,
            )
        _raise_for_papi_status(resp, path)
        return resp.status_code, _safe_json_parse(resp, f"PAPI PATCH {path}")


async def _papi_put(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """Make an authenticated PUT request to PAPI. Returns (status_code, json)."""
    path = _normalize_path(path)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.put(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            json=body,
        )
        if _is_slash_redirect(resp):
            resp = await client.put(
                f"{PAPI_URL}{path}/",
                headers=_headers(),
                json=body,
            )
        _raise_for_papi_status(resp, path)
        return resp.status_code, _safe_json_parse(resp, f"PAPI PUT {path}")


def _safe_json_parse(resp: httpx.Response, label: str) -> Any:
    """Parse JSON from an HTTP response, raising ValueError with context on failure."""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"{label} returned non-JSON response (status {resp.status_code}): {resp.text[:300]}"
        ) from exc


def _json_response(data: Any) -> str:
    """Serialize data to a strict-JSON string safe for MCP transport.

    Uses allow_nan=False so NaN/Infinity raise immediately rather than
    producing non-standard JSON that breaks downstream JS parsers.
    Uses default=str so datetime and other non-serializable types
    degrade to strings instead of crashing.
    """
    return json.dumps(data, indent=2, allow_nan=False, default=str)


def _raise_for_papi_status(resp: httpx.Response, path: str) -> None:
    if resp.status_code == 401:
        raise ValueError(
            f"PAPI returned 401 Unauthorized for {path}. "
            "Your RUNWHEN_TOKEN may be expired or invalid. "
            "Get a fresh token from POST /api/v3/token/ or the RunWhen UI."
        )
    if resp.status_code == 403:
        raise ValueError(
            f"PAPI returned 403 Forbidden for {path}. "
            "You may not have access to this workspace or resource."
        )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Script validation helpers
# ---------------------------------------------------------------------------


def _validate_script(script: str, interpreter: str, task_type: str) -> list[str]:
    """Validate a script against the RunWhen contract. Returns a list of warnings."""
    warnings: list[str] = []

    if interpreter == "python":
        if not re.search(r"^def\s+main\s*\(", script, re.MULTILINE):
            warnings.append("Script must define a top-level main() function.")
        if re.search(r"^main\s*\(", script, re.MULTILINE):
            warnings.append("Do not call main() directly — the runner invokes it.")
        if re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', script):
            warnings.append('Do not use if __name__ == "__main__" — the runner invokes main().')
        if task_type == "sli" and "return" in script and not re.search(r"return\s+[\d.]", script):
            warnings.append("SLI main() should return a float between 0 and 1.")
    elif interpreter == "bash":
        if not re.search(r"^main\s*\(\s*\)", script, re.MULTILINE):
            warnings.append("Script must define a main() function.")
        if task_type == "task" and ">&3" not in script and "> /dev/fd/3" not in script:
            warnings.append("Bash task should write issue JSON to file descriptor 3 (>&3).")
        if task_type == "sli" and ">&3" not in script and "> /dev/fd/3" not in script:
            warnings.append("Bash SLI should write metric value to file descriptor 3 (>&3).")

    return warnings


def _extract_env_vars(script: str, interpreter: str) -> list[str]:
    """Extract environment variable names referenced in a script."""
    builtin_vars = {
        "HOME",
        "USER",
        "PATH",
        "SHELL",
        "PWD",
        "OLDPWD",
        "TERM",
        "LANG",
        "LC_ALL",
        "HOSTNAME",
        "RANDOM",
        "LINENO",
        "SECONDS",
        "PIPESTATUS",
        "BASH_SOURCE",
        "FUNCNAME",
        "IFS",
        "PS1",
        "PS2",
        "_",
    }
    found: set[str] = set()

    if interpreter == "python":
        for m in re.finditer(
            r'os\.environ(?:\.get)?\s*\(\s*["\'](\w+)["\']'
            r'|os\.getenv\s*\(\s*["\'](\w+)["\']',
            script,
        ):
            found.add(m.group(1) or m.group(2))
    elif interpreter == "bash":
        for m in re.finditer(r"\$\{?(\w+)\}?", script):
            name = m.group(1)
            if name not in builtin_vars and not name.isdigit():
                found.add(name)

    return sorted(found)


VALID_ACCESS_TAGS = ("read-write", "read-only")
VALID_DATA_TAGS = ("logs-bulk", "config", "logs-stacktrace")


def _ensure_required_tags(
    tags: list[dict[str, str]] | None,
    access: str,
    data: str,
) -> list[dict[str, str]]:
    """Ensure the required ``access`` and ``data`` tags are present.

    User-supplied tags are preserved (including duplicate names).
    ``access`` and ``data`` entries are added or replaced so they always
    reflect the caller's intent.
    """
    result: list[dict[str, str]] = []
    for tag in tags or []:
        if tag["name"] not in ("access", "data"):
            result.append(tag)
    result.append({"name": "access", "value": access})
    result.append({"name": "data", "value": data})
    return result


def _build_slx_yaml(
    workspace: str,
    slx_name: str,
    alias: str,
    statement: str,
    owners: list[str],
    tags: list[dict[str, str]] | None = None,
    image_url: str | None = None,
    access: str = "read-write",
    data: str = "logs-bulk",
    additional_context: dict[str, Any] | None = None,
) -> str:
    """Generate slx.yaml content."""
    spec: dict[str, Any] = {
        "alias": alias,
        "imageURL": image_url or GENERIC_SLX_ICON,
        "statement": statement,
        "owners": owners,
        "tags": _ensure_required_tags(tags, access, data),
    }
    if additional_context:
        spec["additionalContext"] = additional_context

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "ServiceLevelX",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
            "annotations": {
                "internal.runwhen.com/manually-created": "true",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


async def _get_debugslx(workspace: str) -> dict[str, Any]:
    """Fetch the workspace's debugslx runbook data.

    The debugslx is the built-in SLX that the Tool Builder runtime uses.
    Its runbook response contains the codebundle configuration (ref, repoUrl)
    and the available runner locations under ``status.runnerLocations``.

    Returns an empty dict when the debugslx is unreachable.
    """
    try:
        data = await _papi_get(f"/api/v3/workspaces/{workspace}/slxs/debugslx/runbook")
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


async def _get_authorized_locations(workspace: str) -> list[dict[str, Any]]:
    """Fetch authorized runner locations for *workspace*.

    Uses the dedicated ``authorizedlocations`` endpoint — the same one the
    platform UI uses.  This always includes at least the public runner.
    Falls back to the workspace's debugslx status when the endpoint is
    unavailable (older PAPI versions).
    """
    try:
        data = await _papi_get(f"/api/v3/workspaces/{workspace}/authorizedlocations")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            result = data.get("results") or data.get("locations") or []
            return result if isinstance(result, list) else []
    except Exception:
        pass

    # Fallback: try the platform-wide /api/v3/locations endpoint (PAPI v2)
    try:
        data = await _papi_get("/api/v3/locations")
        results = data.get("results", []) if isinstance(data, dict) else []
        if results:
            return [
                {"label": loc.get("name", ""), "value": loc.get("name", "")}
                for loc in results
                if loc.get("name")
            ]
    except Exception:
        pass

    # Final fallback: debugslx runnerLocations
    data = await _get_debugslx(workspace)
    runner_locations = data.get("status", {}).get("runnerLocations", [])
    return [
        {
            "label": rl.get("location", ""),
            "value": rl.get("location", ""),
            "locationUUID": rl.get("locationUUID", rl.get("location", "")),
            "lastUpdated": rl.get("lastUpdated"),
        }
        for rl in runner_locations
        if rl.get("location")
    ]


async def _infer_location_from_slxs(workspace: str) -> str | None:
    """Inspect existing SLX runbook configs to find the most-used location.

    Checks the debugslx first (cheap), then samples a handful of other SLX
    runbooks and returns the location that appears most often.
    """
    location_counts: dict[str, int] = {}

    # 1. Check debugslx spec.location (already cached in many flows)
    dbg = await _get_debugslx(workspace)
    loc = dbg.get("spec", {}).get("location")
    if loc:
        location_counts[loc] = location_counts.get(loc, 0) + 1

    # 2. Sample a few SLX runbooks for their spec.location
    try:
        slxs_data = await _papi_get(f"/api/v3/workspaces/{workspace}/slxs")
        slxs_list = (
            slxs_data.get("results", slxs_data) if isinstance(slxs_data, dict) else slxs_data
        )
        if isinstance(slxs_list, list):
            sampled = 0
            for slx_item in slxs_list:
                name = slx_item.get("shortName") or slx_item.get("name", "")
                if not name or name == "debugslx":
                    continue
                try:
                    rb = await _papi_get(f"/api/v3/workspaces/{workspace}/slxs/{name}/runbook")
                    rb_loc = rb.get("spec", {}).get("location") if isinstance(rb, dict) else None
                    if rb_loc:
                        location_counts[rb_loc] = location_counts.get(rb_loc, 0) + 1
                except Exception:
                    pass
                sampled += 1
                if sampled >= 5:
                    break
    except Exception:
        pass

    if not location_counts:
        return None
    return max(location_counts, key=location_counts.get)  # type: ignore[arg-type]


def _loc_name(loc: dict[str, Any]) -> str:
    """Extract the usable name/value from an authorized-location dict."""
    return loc.get("value") or loc.get("location") or loc.get("name") or ""


async def _resolve_location(workspace: str, location: str) -> str:
    """Return *location* if non-empty, otherwise auto-resolve from the workspace.

    Resolution strategy (private locations first, public as last resort):

    1. If exactly one private (non-public) location exists, use it.
    2. If multiple private locations exist, inspect existing SLX runbook
       configs to pick the one that's most commonly used.  If still
       ambiguous, raise an error listing the options so the caller can
       ask the user to choose.
    3. If no private locations exist, fall back to the public runner.
    """
    if location:
        return location

    all_locations = await _get_authorized_locations(workspace)
    if not all_locations:
        raise ValueError(
            f"No runner locations found for workspace '{workspace}'. "
            "Ensure at least one runner is registered. "
            "Check the workspace configuration or contact your admin."
        )

    private = [loc for loc in all_locations if loc.get("type") != "public"]
    public = [loc for loc in all_locations if loc.get("type") == "public"]

    # --- Private locations (preferred) ---
    if len(private) == 1:
        name = _loc_name(private[0])
        if name:
            return name

    if len(private) > 1:
        # Disambiguate by inspecting existing SLX runbook configurations.
        inferred = await _infer_location_from_slxs(workspace)
        private_names = {_loc_name(loc) for loc in private if _loc_name(loc)}
        if inferred and inferred in private_names:
            return inferred
        # Still ambiguous — list the options for the agent to present.
        opts = ", ".join(sorted(private_names))
        raise ValueError(
            f"Multiple runner locations available for workspace '{workspace}': "
            f"{opts}. Please specify which location to use via the 'location' "
            "parameter."
        )

    # --- No private locations; fall back to public ---
    for loc in public:
        name = _loc_name(loc)
        if name:
            return name

    raise ValueError(
        f"No runner locations found for workspace '{workspace}'. "
        "Ensure at least one runner is registered. "
        "Check the workspace configuration or contact your admin."
    )


_SLX_NAME_MAX_LEN = 63
_SLX_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _validate_slx_name(slx_name: str) -> None:
    """Validate an SLX short name.

    Raises ``ValueError`` with a user-friendly message when the name is
    invalid.  Rules enforced:
      - non-empty
      - max 63 characters (K8s label-value limit)
      - lowercase kebab-case: ``[a-z0-9]`` with interior hyphens
      - no leading/trailing hyphens, no consecutive hyphens
    """
    if not slx_name:
        raise ValueError("SLX name must not be empty.")
    if len(slx_name) > _SLX_NAME_MAX_LEN:
        raise ValueError(
            f"SLX name is {len(slx_name)} characters — "
            f"max allowed is {_SLX_NAME_MAX_LEN}. "
            "Shorten the name and try again."
        )
    if not _SLX_NAME_RE.match(slx_name):
        raise ValueError(
            f"Invalid SLX name: {slx_name!r}. "
            "Names must be lowercase kebab-case (a-z, 0-9, hyphens), "
            "start and end with an alphanumeric character, "
            "and contain no consecutive hyphens."
        )
    if "--" in slx_name:
        raise ValueError(
            f"Invalid SLX name: {slx_name!r}. "
            "Consecutive hyphens ('--') are reserved for internal naming."
        )


async def _get_codebundle_ref(workspace: str) -> str:
    """Resolve the codebundle branch used by this workspace's tool-builder runtime.

    Falls back to ``"main"`` when the debugslx is unreachable.
    """
    data = await _get_debugslx(workspace)
    ref = data.get("codeBundleRef") or data.get("spec", {}).get("codeBundle", {}).get("ref")
    return ref or "main"


def _build_runbook_yaml(
    workspace: str,
    slx_name: str,
    script_b64: str,
    interpreter: str,
    task_title: str,
    location: str,
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    codebundle_ref: str | None = None,
) -> str:
    """Generate runbook.yaml content for a Tool Builder task."""
    config_provided = [
        {"name": "TASK_TITLE", "value": task_title},
        {"name": "GEN_CMD", "value": script_b64},
        {"name": "INTERPRETER", "value": interpreter},
    ]

    env_vars = env_vars or {}
    secret_vars = secret_vars or {}

    config_provided.append({"name": "CONFIG_ENV_MAP", "value": json.dumps(env_vars)})
    config_provided.append(
        {"name": "SECRET_ENV_MAP", "value": json.dumps(list(secret_vars.keys()))}
    )

    for k, v in env_vars.items():
        config_provided.append({"name": k, "value": v})

    secrets_provided = [{"name": k, "workspaceKey": v} for k, v in secret_vars.items()]

    bundle = dict(RB_CODE_BUNDLE)
    if codebundle_ref:
        bundle["ref"] = codebundle_ref

    spec: dict[str, Any] = {
        "location": location,
        "codeBundle": bundle,
        "configProvided": config_provided,
    }
    if secrets_provided:
        spec["secretsProvided"] = secrets_provided

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "Runbook",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
            "annotations": {
                "internal.runwhen.com/manually-created": "true",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _build_sli_yaml(
    workspace: str,
    slx_name: str,
    script_b64: str,
    interpreter: str,
    location: str,
    interval_seconds: int = 300,
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    codebundle_ref: str | None = None,
) -> str:
    """Generate sli.yaml content for a Tool Builder SLI."""
    config_provided = [
        {"name": "GEN_CMD", "value": script_b64},
        {"name": "INTERPRETER", "value": interpreter},
    ]

    env_vars = env_vars or {}
    secret_vars = secret_vars or {}

    config_provided.append({"name": "CONFIG_ENV_MAP", "value": json.dumps(env_vars)})
    config_provided.append(
        {"name": "SECRET_ENV_MAP", "value": json.dumps(list(secret_vars.keys()))}
    )

    for k, v in env_vars.items():
        config_provided.append({"name": k, "value": v})

    secrets_provided = [{"name": k, "workspaceKey": v} for k, v in secret_vars.items()]

    bundle = dict(SLI_CODE_BUNDLE)
    if codebundle_ref:
        bundle["ref"] = codebundle_ref

    spec: dict[str, Any] = {
        "locations": [location],
        "displayUnitsLong": "OK",
        "displayUnitsShort": "ok",
        "intervalSeconds": interval_seconds,
        "intervalStrategy": "intermezzo",
        "alertConfig": {
            "tasks": {
                "persona": "eager-edgar",
                "sessionTTL": "10m",
            }
        },
        "codeBundle": bundle,
        "configProvided": config_provided,
    }
    if secrets_provided:
        spec["secretsProvided"] = secrets_provided

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "ServiceLevelIndicator",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _build_cron_sli_yaml(
    workspace: str,
    slx_name: str,
    location: str,
    cron_schedule: str,
    interval_seconds: int = 60,
    target_slx: str | None = None,
    dry_run: bool = False,
) -> str:
    """Generate sli.yaml for the cron-scheduler-sli codebundle.

    This creates an SLI that triggers the parent SLX's runbook on a cron
    schedule. If target_slx is empty, the scheduler triggers the runbook
    of the SLX it's attached to (self-scheduling pattern).

    Note: the cron-SLI always uses ``rw-workspace-utils`` on ``main``.
    The auto-detected ``codebundle_ref`` from the debugslx applies to
    ``rw-generic-codecollection`` only and must NOT be applied here.
    """
    config_provided = [
        {"name": "CRON_SCHEDULE", "value": cron_schedule},
        {"name": "DRY_RUN", "value": "true" if dry_run else "false"},
    ]
    if target_slx:
        config_provided.append({"name": "TARGET_SLX", "value": target_slx})

    bundle = dict(CRON_SLI_CODE_BUNDLE)

    spec: dict[str, Any] = {
        "locations": [location],
        "displayUnitsLong": "OK",
        "displayUnitsShort": "ok",
        "intervalSeconds": interval_seconds,
        "intervalStrategy": "intermezzo",
        "alertConfig": {
            "tasks": {
                "persona": "eager-edgar",
                "sessionTTL": "10m",
            }
        },
        "codeBundle": bundle,
        "configProvided": config_provided,
    }

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "ServiceLevelIndicator",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Registry codebundle YAML builders (different shape from Tool Builder)
# ---------------------------------------------------------------------------


def _build_registry_runbook_yaml(
    workspace: str,
    slx_name: str,
    repo_url: str,
    path_to_robot: str,
    location: str,
    config_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    ref: str = "main",
) -> str:
    """Generate runbook.yaml for a registry codebundle (no inline script)."""
    config_provided = [{"name": k, "value": v} for k, v in (config_vars or {}).items()]
    secrets_provided = [{"name": k, "workspaceKey": v} for k, v in (secret_vars or {}).items()]

    spec: dict[str, Any] = {
        "location": location,
        "codeBundle": {
            "repoUrl": repo_url,
            "ref": ref,
            "pathToRobot": path_to_robot,
        },
        "configProvided": config_provided,
    }
    if secrets_provided:
        spec["secretsProvided"] = secrets_provided

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "Runbook",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
            "annotations": {
                "internal.runwhen.com/manually-created": "true",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _build_registry_sli_yaml(
    workspace: str,
    slx_name: str,
    repo_url: str,
    path_to_robot: str,
    location: str,
    config_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    ref: str = "main",
    interval_seconds: int = 300,
    description: str = "",
) -> str:
    """Generate sli.yaml for a registry codebundle (no inline script)."""
    config_provided = [{"name": k, "value": v} for k, v in (config_vars or {}).items()]
    secrets_provided = [{"name": k, "workspaceKey": v} for k, v in (secret_vars or {}).items()]

    spec: dict[str, Any] = {
        "locations": [location],
        "displayUnitsLong": "OK",
        "displayUnitsShort": "ok",
        "intervalSeconds": interval_seconds,
        "intervalStrategy": "intermezzo",
        "codeBundle": {
            "repoUrl": repo_url,
            "ref": ref,
            "pathToRobot": path_to_robot,
        },
        "configProvided": config_provided,
        "alertConfig": {
            "tasks": {
                "persona": "eager-edgar",
                "sessionTTL": "10m",
            }
        },
    }
    if description:
        spec["description"] = description
    if secrets_provided:
        spec["secretsProvided"] = secrets_provided

    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "ServiceLevelIndicator",
        "metadata": {
            "name": f"{workspace}--{slx_name}",
            "labels": {
                "workspace": workspace,
                "slx": f"{workspace}--{slx_name}",
            },
            "annotations": {
                "internal.runwhen.com/manually-created": "true",
            },
        },
        "spec": spec,
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


async def _consume_agentfarm_sse(
    url: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST to an AgentFarm SSE endpoint and collect the full response.

    AgentFarm streams SSE events. We collect all chunks and assemble
    the final message, widgets, function calls, etc.
    """
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    assembled_message = ""
    widgets: list[Any] = []
    function_calls: list[Any] = []
    function_responses: list[Any] = []
    internal_tool_calls: list[Any] = []
    session_id: str | None = None
    error_message: str | None = None
    resources: list[Any] = []
    export_url: str | None = None

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code == 401:
                    raise ValueError(
                        "AgentFarm returned 401 Unauthorized. "
                        "Your RUNWHEN_TOKEN may be expired or invalid."
                    )
                if resp.status_code == 403:
                    raise ValueError(
                        "AgentFarm returned 403 Forbidden. "
                        "You may not have access to this workspace."
                    )
                resp.raise_for_status()
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if not data_str.strip() or data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if event.get("error"):
                            error_message = event["error"]
                            continue

                        if event.get("sessionId") and not session_id:
                            session_id = event["sessionId"]

                        msg = event.get("message") or ""
                        if msg:
                            assembled_message += msg

                        for w in event.get("widgets") or []:
                            widgets.append(w)
                        for fc in event.get("functionCalls") or []:
                            function_calls.append(fc)
                        for fr in event.get("functionResponses") or []:
                            function_responses.append(fr)
                        for itc in event.get("internalToolCalls") or []:
                            internal_tool_calls.append(itc)
                        for r in event.get("resources") or []:
                            resources.append(r)
                        if event.get("exportUrl"):
                            export_url = event["exportUrl"]
                        elif event.get("export_url"):
                            export_url = event["export_url"]
        except httpx.ConnectError as exc:
            raise ValueError(
                f"Cannot connect to AgentFarm at {url}. "
                f"Check RW_API_URL is correct (AgentFarm URL is derived from it). Error: {exc}"
            ) from exc

    result: dict[str, Any] = {}
    if error_message:
        result["error"] = error_message
    if assembled_message:
        result["message"] = assembled_message
    if session_id:
        result["sessionId"] = session_id
    if widgets:
        result["widgets"] = widgets
    if function_calls:
        result["functionCalls"] = function_calls
    if function_responses:
        result["functionResponses"] = function_responses
    if internal_tool_calls:
        result["internalToolCalls"] = internal_tool_calls
    if resources:
        result["resources"] = resources
    if export_url:
        result["chatExportLink"] = export_url
    return result


async def _fetch_chat_export_url(workspace: str, user_id: str, session_id: str) -> str | None:
    """Request a signed chat-export URL for this session from AgentFarm.
    Returns the path (e.g. /workspace/{ws}/chat-export/{token}) or None on failure.
    """
    token = _get_token()
    url = f"{AGENTFARM_URL}/api/v1/workspaces/{workspace}/chat-export-url"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"userId": user_id, "sessionId": session_id}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get("exportUrl") or data.get("export_url")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def workspace_chat(
    message: str = Field(
        description="Your question or request about the workspace infrastructure."
    ),
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    persona_name: str = Field(
        default="default", description="AI persona to use (default: 'default')."
    ),
    session_id: Annotated[
        str | None, Field(description="Optional session ID to continue a previous conversation.")
    ] = None,
) -> str:
    """Ask the RunWhen AI assistant about your infrastructure.

    This is the PRIMARY tool for investigating infrastructure. It sends your
    message to the RunWhen workspace AI agent which has ~25+ internal tools
    including semantic search, keyword grep, resource graph traversal, issue
    correlation, knowledge base lookup, and data analysis.

    PREFER THIS TOOL over direct read/query tools (get_workspace_issues,
    get_workspace_slxs, search_workspace, etc.) for any question that
    involves searching by topic, keyword, or context — e.g. "issues related
    to neo4j", "what's failing in namespace X?", "health of the watcher
    cluster". workspace_chat produces materially better answers because it
    can search, filter, and correlate across all workspace data internally.

    Use direct tools instead ONLY for: executing tasks (`run_slx`), task
    authoring, registry operations, chat config CRUD, KB mutations, or when
    you specifically need raw structured JSON for programmatic processing.

    Returns:
        JSON with message, sessionId, widgets, chatUrl (full browser URL to
        continue this session in the RunWhen UI — run tasks, review history),
        and chatExportLink (shareable chat-export path when available).
    """
    ws = await _resolve_workspace(workspace_name)
    user_id = await _get_user_email()

    url = f"{AGENTFARM_URL}/api/v1/workspaces/{ws}/chat-pro-sse"
    body: dict[str, Any] = {
        "message": message,
        "workspaceName": ws,
        "personaName": persona_name,
        "userId": user_id,
        "sessionId": session_id,
    }

    result = await _consume_agentfarm_sse(url, body)
    sid = result.get("sessionId")
    if sid:
        app_base = _runwhen_app_base_url()
        if app_base:
            result["chatUrl"] = _format_workspace_chat_browser_url(app_base, ws, sid)
    if sid and "chatExportLink" not in result:
        link = await _fetch_chat_export_url(ws, user_id, sid)
        if link:
            result["chatExportLink"] = link
    return _json_response(result)


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces you have access to.

    Returns workspace names, display names, and basic metadata.
    """
    summary = await _fetch_workspace_list()
    return _json_response(summary)


@mcp.tool()
async def get_workspace_chat_config(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    persona_name: Annotated[
        str | None, Field(description="Optional persona for persona-scoped rules/commands.")
    ] = None,
) -> str:
    """Get resolved chat rules and commands for a workspace.

    Returns the list of rules and commands that apply to the workspace (and optional
    persona). These are the same rules and commands the workspace chat assistant sees.
    Response includes metadata only (id, name, scope); full rule/command content
    is not included in this endpoint.
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {}
    if persona_name:
        params["persona_name"] = persona_name
    try:
        data = await _papi_get(
            f"/api/v3/workspaces/{ws}/chat-config/resolved",
            params=params or None,
        )
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})
    return _json_response(data)


@mcp.tool()
async def list_chat_rules(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    scope_type: Annotated[
        str | None, Field(description="Filter by scope (platform, org, workspace, persona, user).")
    ] = None,
    scope_id: Annotated[
        str | None,
        Field(description="Filter by scope ID (e.g. workspace name, or None for platform)."),
    ] = None,
    is_active: Annotated[bool | None, Field(description="Filter by active status.")] = None,
    page: int = Field(default=1, description="Page number (1-based)."),
    page_size: int = Field(default=50, description="Items per page (1-200)."),
) -> str:
    """List chat rules (workspace chat rules).

    Uses AgentFarm internal API; may require network access.
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if scope_type is not None:
        params["scope_type"] = scope_type
    if scope_id is not None:
        params["scope_id"] = scope_id
    if is_active is not None:
        params["is_active"] = is_active
    try:
        data = await _papi_get(f"/api/v3/workspaces/{ws}/chat-config/rules", params=params)
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def get_chat_rule(
    rule_id: int = Field(description="The rule ID to retrieve."),
    workspace_name: str = Field(description="The workspace the rule belongs to (e.g. 't-oncall')."),
) -> str:
    """Get a single chat rule by ID (full content)."""
    ws = await _resolve_workspace(workspace_name)
    try:
        data = await _papi_get(f"/api/v3/workspaces/{ws}/chat-config/rules/{rule_id}")
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def create_chat_rule(
    name: str = Field(description="Human-readable name for the rule."),
    rule_content: str = Field(description="Markdown content of the rule."),
    scope_type: str = Field(description="One of platform, org, workspace, persona, user."),
    workspace_name: str = Field(
        description="The workspace to create the rule in (e.g. 't-oncall')."
    ),
    scope_id: Annotated[
        str | None, Field(description="Scope ID (null for platform; workspace name for workspace).")
    ] = None,
    is_active: bool = Field(default=True, description="Whether the rule is active."),
) -> str:
    """Create a chat rule. Uses AgentFarm internal API."""
    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {
        "name": name,
        "rule_content": rule_content,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "is_active": is_active,
    }
    try:
        status_code, data = await _papi_post(f"/api/v3/workspaces/{ws}/chat-config/rules", body)
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def update_chat_rule(
    rule_id: int = Field(description="The rule ID to update."),
    workspace_name: str = Field(description="The workspace the rule belongs to (e.g. 't-oncall')."),
    name: Annotated[str | None, Field(description="New rule name.")] = None,
    rule_content: Annotated[str | None, Field(description="New markdown content.")] = None,
    scope_type: Annotated[str | None, Field(description="New scope type.")] = None,
    scope_id: Annotated[str | None, Field(description="New scope ID.")] = None,
    is_active: Annotated[bool | None, Field(description="Set active/inactive.")] = None,
) -> str:
    """Update an existing chat rule by ID."""
    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if rule_content is not None:
        body["rule_content"] = rule_content
    if scope_type is not None:
        body["scope_type"] = scope_type
    if scope_id is not None:
        body["scope_id"] = scope_id
    if is_active is not None:
        body["is_active"] = is_active
    try:
        _, data = await _papi_put(f"/api/v3/workspaces/{ws}/chat-config/rules/{rule_id}", body)
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def list_chat_commands(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    scope_type: Annotated[
        str | None, Field(description="Filter by scope (platform, org, workspace, persona, user).")
    ] = None,
    scope_id: Annotated[str | None, Field(description="Filter by scope ID.")] = None,
    is_active: Annotated[bool | None, Field(description="Filter by active status.")] = None,
    page: int = Field(default=1, description="Page number (1-based)."),
    page_size: int = Field(default=50, description="Items per page (1-200)."),
) -> str:
    """List chat commands (slash-command instructions)."""
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if scope_type is not None:
        params["scope_type"] = scope_type
    if scope_id is not None:
        params["scope_id"] = scope_id
    if is_active is not None:
        params["is_active"] = is_active
    try:
        data = await _papi_get(f"/api/v3/workspaces/{ws}/chat-config/commands", params=params)
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def get_chat_command(
    command_id: int = Field(description="The command ID to retrieve."),
    workspace_name: str = Field(
        description="The workspace the command belongs to (e.g. 't-oncall')."
    ),
) -> str:
    """Get a single chat command by ID (full content)."""
    ws = await _resolve_workspace(workspace_name)
    try:
        data = await _papi_get(f"/api/v3/workspaces/{ws}/chat-config/commands/{command_id}")
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def create_chat_command(
    name: str = Field(description="Command name (alphanumeric, underscore, or hyphen only)."),
    command_content: str = Field(description="Markdown content of the command."),
    scope_type: str = Field(description="One of platform, org, workspace, persona, user."),
    workspace_name: str = Field(
        description="The workspace to create the command in (e.g. 't-oncall')."
    ),
    scope_id: Annotated[
        str | None, Field(description="Scope ID (null for platform; workspace name for workspace).")
    ] = None,
    description: Annotated[
        str | None, Field(description="Optional description for the command.")
    ] = None,
    is_active: bool = Field(default=True, description="Whether the command is active."),
) -> str:
    """Create a chat command (slash-command). Name must be alphanumeric, underscore, or hyphen only.

    Commands are invoked in chat as [/label](cmd://name).
    """
    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {
        "name": name,
        "command_content": command_content,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "is_active": is_active,
    }
    if description is not None:
        body["description"] = description
    try:
        status_code, data = await _papi_post(f"/api/v3/workspaces/{ws}/chat-config/commands", body)
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def update_chat_command(
    command_id: int = Field(description="The command ID to update."),
    workspace_name: str = Field(
        description="The workspace the command belongs to (e.g. 't-oncall')."
    ),
    name: Annotated[str | None, Field(description="New command name.")] = None,
    command_content: Annotated[str | None, Field(description="New markdown content.")] = None,
    description: Annotated[str | None, Field(description="New description.")] = None,
    scope_type: Annotated[str | None, Field(description="New scope type.")] = None,
    scope_id: Annotated[str | None, Field(description="New scope ID.")] = None,
    is_active: Annotated[bool | None, Field(description="Set active/inactive.")] = None,
) -> str:
    """Update an existing chat command by ID."""
    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if command_content is not None:
        body["command_content"] = command_content
    if description is not None:
        body["description"] = description
    if scope_type is not None:
        body["scope_type"] = scope_type
    if scope_id is not None:
        body["scope_id"] = scope_id
    if is_active is not None:
        body["is_active"] = is_active
    try:
        _, data = await _papi_put(
            f"/api/v3/workspaces/{ws}/chat-config/commands/{command_id}", body
        )
        return _json_response(data)
    except (ValueError, httpx.HTTPStatusError) as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def get_workspace_issues(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    severity: Annotated[
        int | None, Field(description="Filter: 1=critical, 2=high, 3=medium, 4=low.")
    ] = None,
    limit: int = Field(default=20, description="Max issues to return."),
    since: Annotated[
        str | None,
        Field(
            description="ISO 8601 lower bound for latest occurrence (e.g. '2026-03-29T14:00:00Z')."
        ),
    ] = None,
) -> str:
    """Get current issues for a workspace (structured JSON).

    Issues represent detected problems in your infrastructure that
    RunWhen has identified through automated health checks.

    NOTE: For questions like "issues related to neo4j" or "what's failing
    in namespace X", prefer `workspace_chat` — it has semantic search and
    keyword filtering that produce materially better results. Use this tool
    only when you need raw JSON for programmatic processing.
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"limit": limit}
    if severity is not None:
        params["severity"] = severity
    if since:
        params["latest_occurrence_at__gte"] = since
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues", params=params)
    return _json_response(data)


@mcp.tool()
async def get_workspace_slxs(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
) -> str:
    """List SLXs (Service Level eXperiences) in a workspace (structured JSON).

    SLXs are the fundamental unit of work in RunWhen — each represents a
    health check, task, or automation runbook for a piece of infrastructure.

    NOTE: For questions like "which SLXs monitor neo4j?" or "find health
    checks for namespace X", prefer `workspace_chat` — it can search and
    correlate SLXs with resources semantically. Use this tool only when you
    need raw JSON for programmatic processing.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs")
    return _json_response(data)


@mcp.tool()
async def get_run_sessions(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    limit: int = Field(default=20, description="Max run sessions to return."),
) -> str:
    """Get recent run sessions for a workspace (structured JSON).

    Run sessions are executions of SLX runbooks — they contain the output
    of health checks, troubleshooting tasks, and automation runs.

    NOTE: For investigative questions like "what ran recently for service X?"
    or "show me recent failures", prefer `workspace_chat` — it can search,
    filter, and correlate run sessions with issues and resources. Use this
    tool only when you need raw JSON for programmatic processing.
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"page": 1, "page-size": limit}
    data = await _papi_get(f"/api/v3/workspaces/{ws}/runsessions", params=params)
    return _json_response(data)


@mcp.tool()
async def get_workspace_config_index(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
) -> str:
    """Get the workspace configuration index (structured JSON).

    Returns an overview of all configured resources, SLXs, and their
    relationships in the workspace. Useful for understanding what's
    monitored and how things are connected.

    NOTE: For questions like "what's monitored in namespace X?" or "how are
    resources connected?", prefer `workspace_chat` — it can traverse the
    resource graph and provide contextual answers. Use this tool only when
    you need the raw configuration index for programmatic processing.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/workspace-configuration-index")
    return _json_response(data)


@mcp.tool()
async def get_issue_details(
    issue_id: str = Field(description="The issue ID to look up."),
    workspace_name: str = Field(
        description="The workspace the issue belongs to (e.g. 't-oncall')."
    ),
) -> str:
    """Get detailed information about a specific issue (structured JSON).

    NOTE: Prefer `workspace_chat` for investigative questions about an issue
    (e.g. root cause, related resources, next steps). Use this tool only
    when you already have an issue ID and need raw JSON.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues/{issue_id}")
    return _json_response(data)


@mcp.tool()
async def get_slx_runbook(
    slx_name: str = Field(description="The SLX short name."),
    workspace_name: str = Field(description="The workspace the SLX belongs to (e.g. 't-oncall')."),
) -> str:
    """Get the runbook for a specific SLX (structured JSON).

    Returns the runbook definition including what tasks it runs,
    how they're configured, and what they check.

    NOTE: For questions like "what does this SLX do?" or "what tasks does it
    run?", prefer `workspace_chat` — it provides contextual explanations.
    Use this tool when you need the raw runbook YAML/JSON (e.g. for task
    authoring or programmatic inspection).
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook")
    return _json_response(data)


@mcp.tool()
async def search_workspace(
    query: str = Field(description="Search query string."),
    workspace_name: str = Field(description="The workspace to search (e.g. 't-oncall')."),
) -> str:
    """Search for tasks, resources, and configuration in a workspace.

    Uses the workspace's task search / autocomplete to find matching items.

    NOTE: Prefer `workspace_chat` for most search queries — it uses
    semantic search and keyword grep across issues, resources, SLXs, and
    run sessions with much richer results. Use this tool only as a
    lightweight autocomplete fallback.
    """
    ws = await _resolve_workspace(workspace_name)
    _, data = await _papi_post(
        f"/api/v3/workspaces/{ws}/autocomplete",
        {"query": query},
    )
    return _json_response(data)


# ---------------------------------------------------------------------------
# Knowledge Base (Notes) Tools
# ---------------------------------------------------------------------------

VALID_KB_STATUSES = {"active", "deprecated"}


@mcp.tool()
async def list_knowledge_base_articles(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
    status: Annotated[
        str | None,
        Field(description="Filter by status — 'active' or 'deprecated'. Returns all if omitted."),
    ] = None,
    search: Annotated[str | None, Field(description="Search within article content.")] = None,
    limit: int = Field(default=50, description="Max articles to return (max 200)."),
) -> str:
    """List Knowledge Base articles (notes) in a workspace (structured JSON).

    Returns KB articles that feed the workspace's Knowledge Overlay Graph.
    Articles can contain operational knowledge, runbook context, architecture
    notes, or any information useful for troubleshooting.

    NOTE: For questions like "what do we know about service X?", prefer
    `workspace_chat` — it searches KB articles semantically. Use this tool
    for programmatic KB management (listing, filtering by status).
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"limit": min(limit, 200), "offset": 0}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    data = await _papi_get(f"/api/v3/workspaces/{ws}/notes", params=params)
    return _json_response(data)


@mcp.tool()
async def get_knowledge_base_article(
    note_id: str = Field(description="The UUID of the KB article to retrieve."),
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
) -> str:
    """Get a specific Knowledge Base article by ID."""
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/notes/{note_id}")
    return _json_response(data)


@mcp.tool()
async def create_knowledge_base_article(
    content: str = Field(
        description="The article content (plain text or markdown, max 20000 chars)."
    ),
    workspace_name: str = Field(description="The workspace to create in (e.g. 't-oncall')."),
    resource_paths: Annotated[
        list[str] | None,
        Field(description="Canonical resource paths (e.g. ['kubernetes/namespace/prod'])."),
    ] = None,
    abstract_entities: Annotated[
        list[str] | None,
        Field(description="Entity tokens for indexing (e.g. ['oom-killed', 'memory-limits'])."),
    ] = None,
) -> str:
    """Create a new Knowledge Base article in a workspace.

    KB articles are indexed into the Knowledge Overlay Graph and become
    searchable by the workspace AI assistant and other tools.

    Content should be informative operational knowledge — architecture notes,
    troubleshooting guides, runbook context, dependency documentation, etc.
    """
    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {
        "content": content,
        "resourcePaths": resource_paths or [],
        "abstractEntities": abstract_entities or [],
        "status": "active",
    }
    status_code, data = await _papi_post(f"/api/v3/workspaces/{ws}/notes", body)
    result = {
        "status": "created" if status_code == 201 else "ok",
        "workspace": ws,
        "article": data,
    }
    return _json_response(result)


@mcp.tool()
async def update_knowledge_base_article(
    note_id: str = Field(description="The UUID of the KB article to update."),
    workspace_name: str = Field(description="The workspace (e.g. 't-oncall')."),
    content: Annotated[
        str | None, Field(description="Updated article content (max 20000 chars).")
    ] = None,
    resource_paths: Annotated[
        list[str] | None, Field(description="Updated resource paths.")
    ] = None,
    abstract_entities: Annotated[
        list[str] | None, Field(description="Updated entity tokens.")
    ] = None,
    status: Annotated[str | None, Field(description="Set to 'active' or 'deprecated'.")] = None,
    verified: Annotated[
        bool | None, Field(description="Mark as human-verified (true/false).")
    ] = None,
) -> str:
    """Update an existing Knowledge Base article.

    Only provided fields are updated; omitted fields remain unchanged.
    """
    if status and status not in VALID_KB_STATUSES:
        return _json_response(
            {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_KB_STATUSES)}"}
        )

    ws = await _resolve_workspace(workspace_name)
    body: dict[str, Any] = {}
    if content is not None:
        body["content"] = content
    if resource_paths is not None:
        body["resourcePaths"] = resource_paths
    if abstract_entities is not None:
        body["abstractEntities"] = abstract_entities
    if status is not None:
        body["status"] = status
    if verified is not None:
        body["verified"] = verified

    if not body:
        return _json_response({"error": "No fields to update. Provide at least one field."})

    _, data = await _papi_patch(f"/api/v3/workspaces/{ws}/notes/{note_id}", body)
    return _json_response(data)


@mcp.tool()
async def delete_knowledge_base_article(
    note_id: str = Field(description="The UUID of the KB article to delete."),
    workspace_name: str = Field(description="The workspace (e.g. 't-oncall')."),
) -> str:
    """Delete a Knowledge Base article.

    Removes the article from the workspace and the Knowledge Overlay Graph index.
    """
    ws = await _resolve_workspace(workspace_name)
    status_code, data = await _papi_delete(f"/api/v3/workspaces/{ws}/notes/{note_id}")
    result = {
        "status": "deleted" if status_code in (200, 204) else f"status_{status_code}",
        "note_id": note_id,
        "workspace": ws,
    }
    return _json_response(result)


# ---------------------------------------------------------------------------
# CodeBundle Registry (public, no auth required)
# ---------------------------------------------------------------------------


async def _registry_get(path: str, params: dict | None = None) -> httpx.Response:
    """GET against the public CodeBundle Registry API (no auth needed)."""
    url = f"{REGISTRY_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.get(url, params=params)


@mcp.tool()
async def search_registry(
    search: str = Field(
        description="Free-text search (e.g. 'kubernetes pod health', 'postgres backup')."
    ),
    platform: Annotated[
        str | None, Field(description="Filter by platform (e.g. 'Kubernetes', 'GCP', 'AWS').")
    ] = None,
    tags: Annotated[
        str | None, Field(description="Comma-separated support tags (e.g. 'GKE,KUBERNETES').")
    ] = None,
    max_results: int = Field(default=10, description="Max results to return."),
) -> str:
    """Search the RunWhen CodeBundle Registry for reusable automation.

    Use this BEFORE writing a custom script — there may already be a
    production-ready codebundle for the task.  Returns codebundles with
    their tasks, SLIs, required env vars, and deployment metadata.
    """
    params: dict[str, str | int] = {"search": search, "limit": max_results}
    if platform:
        params["platform"] = platform
    if tags:
        params["tags"] = tags

    resp = await _registry_get("/api/v1/codebundles", params=params)
    if resp.status_code != 200:
        return _json_response(
            {"error": f"Registry returned {resp.status_code}", "body": resp.text[:500]}
        )

    data = _safe_json_parse(resp, "Registry GET /api/v1/codebundles")
    bundles = data.get("codebundles", data if isinstance(data, list) else [])

    results = []
    for b in bundles[:max_results]:
        entry: dict = {
            "name": b.get("name"),
            "display_name": b.get("display_name"),
            "slug": b.get("slug"),
            "description": b.get("ai_enhanced_description") or b.get("description"),
            "platform": b.get("platform"),
            "support_tags": b.get("support_tags", []),
            "tasks": b.get("tasks", []),
            "slis": b.get("slis", []),
            "access_level": b.get("access_level"),
            "runbook_source_url": b.get("runbook_source_url"),
        }
        cc = b.get("codecollection")
        if cc:
            entry["codecollection_slug"] = cc.get("slug")
            entry["codecollection_git_url"] = cc.get("git_url")
        ct = b.get("configuration_type", {})
        if ct.get("type") == "Automatically Discovered":
            entry["auto_discoverable"] = True
            entry["resource_types"] = ct.get("resource_types", [])
        results.append(entry)

    return _json_response(
        {
            "total_count": data.get("total_count", len(results)),
            "results": results,
        }
    )


@mcp.tool()
async def get_registry_codebundle(
    collection_slug: str = Field(
        description="The codecollection slug (e.g. 'rw-cli-codecollection')."
    ),
    codebundle_slug: str = Field(
        description="The codebundle slug (e.g. 'k8s-podresources-health')."
    ),
) -> str:
    """Get full details of a specific codebundle from the registry.

    Use after search_registry to get complete information including
    configuration templates, environment variables, and deployment instructions.
    """
    resp = await _registry_get(
        f"/api/v1/collections/{collection_slug}/codebundles/{codebundle_slug}"
    )
    if resp.status_code == 404:
        return _json_response(
            {
                "error": (
                    f"Codebundle '{codebundle_slug}' not found in collection '{collection_slug}'."
                )
            }
        )
    if resp.status_code != 200:
        return _json_response(
            {"error": f"Registry returned {resp.status_code}", "body": resp.text[:500]}
        )

    return resp.text


@mcp.tool()
async def deploy_registry_codebundle(
    slx_name: str = Field(description="Short SLX name (lowercase-kebab-case)."),
    alias: str = Field(description="Human-readable display name (e.g. 'Namespace Health')."),
    statement: str = Field(description="SLX statement (e.g. 'All pods should be running')."),
    repo_url: str = Field(description="Git URL of the codecollection."),
    codebundle_path: str = Field(
        description="Path to codebundle dir (e.g. 'codebundles/k8s-namespace-healthcheck')."
    ),
    location: str = Field(description="Runner location (use get_workspace_locations)."),
    workspace_name: str = Field(description="Target workspace (e.g. 't-oncall')."),
    config_vars: Annotated[
        dict[str, str] | None, Field(description="Codebundle config variables.")
    ] = None,
    secret_vars: Annotated[
        dict[str, str] | None,
        Field(description="Secret mappings (e.g. {'kubeconfig': 'kubeconfig'})."),
    ] = None,
    deploy_runbook: bool = Field(default=True, description="Deploy the runbook (task)."),
    deploy_sli: bool = Field(default=False, description="Also deploy the SLI (health indicator)."),
    sli_description: str = Field(default="", description="Description for the SLI metric."),
    sli_interval_seconds: int = Field(default=300, description="SLI run interval in seconds."),
    ref: str = Field(default="main", description="Git branch/tag for the codecollection."),
    owners: Annotated[
        list[str] | None, Field(description="Owner emails (defaults to current user).")
    ] = None,
    branch: str = Field(default="main", description="Workspace config branch."),
    tags: Annotated[
        list[dict[str, str]] | None, Field(description="Resource tags ({name, value} dicts).")
    ] = None,
    image_url: Annotated[str | None, Field(description="Icon URL for the SLX.")] = None,
    access: str = Field(default="read-only", description="'read-only' or 'read-write'."),
    data: str = Field(
        default="logs-bulk", description="'logs-bulk', 'config', or 'logs-stacktrace'."
    ),
    resource_path: Annotated[
        str | None, Field(description="Resource path for search indexing.")
    ] = None,
    hierarchy: Annotated[
        list[str] | None, Field(description="Tag names for hierarchical grouping.")
    ] = None,
    commit_message: Annotated[str | None, Field(description="Custom commit message.")] = None,
) -> str:
    """Deploy a registry codebundle as an SLX to a workspace.

    Unlike commit_slx (which embeds inline scripts via the Tool Builder
    codebundle), this deploys a pre-built codebundle from its own
    codecollection repository.  The runbook.robot / sli.robot live in the
    codebundle's git repo — no inline script is needed.

    Use search_registry + get_registry_codebundle to find the right
    codebundle, then call this tool with the values from the registry.
    """
    if not deploy_runbook and not deploy_sli:
        return _json_response(
            {"error": ("At least one of deploy_runbook or deploy_sli must be True.")}
        )

    if access not in VALID_ACCESS_TAGS:
        return _json_response(
            {
                "error": (
                    f"Invalid access tag '{access}'. Must be one of: {', '.join(VALID_ACCESS_TAGS)}"
                )
            }
        )
    if data not in VALID_DATA_TAGS:
        return _json_response(
            {"error": f"Invalid data tag '{data}'. Must be one of: {', '.join(VALID_DATA_TAGS)}"}
        )

    try:
        _validate_slx_name(slx_name)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    ws = await _resolve_workspace(workspace_name)

    try:
        location = await _resolve_location(ws, location)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    # Ensure .git suffix on repo URL
    git_url = repo_url if repo_url.endswith(".git") else f"{repo_url}.git"

    # Normalise codebundle_path (strip trailing slashes)
    cb_path = codebundle_path.rstrip("/")

    if owners is None:
        owners = [await _get_user_email()]

    additional_context: dict[str, Any] | None = None
    if resource_path or hierarchy:
        additional_context = {}
        if resource_path:
            additional_context["resourcePath"] = resource_path
        if hierarchy:
            additional_context["hierarchy"] = hierarchy

    slx_yaml = _build_slx_yaml(
        workspace=ws,
        slx_name=slx_name,
        alias=alias,
        statement=statement,
        owners=owners,
        tags=tags,
        image_url=image_url,
        access=access,
        data=data,
        additional_context=additional_context,
    )

    files: dict[str, str] = {"slx.yaml": slx_yaml}
    committed_types: list[str] = []

    if deploy_runbook:
        files["runbook.yaml"] = _build_registry_runbook_yaml(
            workspace=ws,
            slx_name=slx_name,
            repo_url=git_url,
            path_to_robot=f"{cb_path}/runbook.robot",
            location=location,
            config_vars=config_vars,
            secret_vars=secret_vars,
            ref=ref,
        )
        committed_types.append("runbook")

    if deploy_sli:
        files["sli.yaml"] = _build_registry_sli_yaml(
            workspace=ws,
            slx_name=slx_name,
            repo_url=git_url,
            path_to_robot=f"{cb_path}/sli.robot",
            location=location,
            config_vars=config_vars,
            secret_vars=secret_vars,
            ref=ref,
            interval_seconds=sli_interval_seconds,
            description=sli_description,
        )
        committed_types.append("sli")

    type_label = " + ".join(committed_types)
    if not commit_message:
        commit_message = f"Deploy registry codebundle {type_label}: {alias}"

    body = {
        "commit_msg": commit_message,
        "files": files,
    }

    status_code, resp_data = await _papi_post(
        f"/api/v3/workspaces/{ws}/branches/{branch}/slxs/{slx_name}",
        body,
    )

    success = status_code in (200, 201)
    result = {
        "status": "deployed" if success else f"error_{status_code}",
        "slx_name": slx_name,
        "workspace": ws,
        "branch": branch,
        "repo_url": git_url,
        "codebundle_path": cb_path,
        "ref": ref,
        "committed_files": list(files.keys()),
        "committed_types": type_label,
        "config_vars": config_vars or {},
        "response": resp_data,
    }
    return _json_response(result)


# ---------------------------------------------------------------------------
# Workspace Context (RUNWHEN.md)
# ---------------------------------------------------------------------------

RUNWHEN_MD_FILENAME = "RUNWHEN.md"

_workspace_context_cache: dict | None = None


def _find_runwhen_md() -> str | None:
    """Locate a RUNWHEN.md file by convention.

    Search order:
      1. Explicit path via RUNWHEN_CONTEXT_FILE env var (if set)
      2. Current working directory
      3. Walk up parent directories until filesystem root
    """
    if RUNWHEN_CONTEXT_FILE:
        expanded = os.path.expanduser(os.path.expandvars(RUNWHEN_CONTEXT_FILE))
        if os.path.isfile(expanded):
            return os.path.abspath(expanded)
        return None

    current = os.path.abspath(os.getcwd())
    while True:
        candidate = os.path.join(current, RUNWHEN_MD_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return None


def _load_workspace_context(*, force: bool = False) -> dict:
    """Load the RUNWHEN.md file, with caching.

    Returns dict with keys: found (bool), path (str|None), content (str).
    """
    global _workspace_context_cache
    if not force and _workspace_context_cache is not None:
        return _workspace_context_cache

    path = _find_runwhen_md()
    if not path:
        result: dict = {"found": False, "path": None, "content": ""}
        _workspace_context_cache = result
        return result

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        result = {"found": True, "path": path, "content": content}
    except Exception:
        result = {"found": False, "path": path, "content": ""}

    _workspace_context_cache = result
    return result


@mcp.tool()
async def get_workspace_context(
    reload: bool = Field(
        default=False, description="Force re-read from disk (default: False, uses cached version)."
    ),
) -> str:
    """Get domain-specific context for building RunWhen tasks.

    Reads the project's RUNWHEN.md file, which contains infrastructure
    conventions, database access rules, naming patterns, architectural
    knowledge, and other constraints that scripts must follow.

    The file is auto-discovered by walking up from the current working
    directory. Override with the RUNWHEN_CONTEXT_FILE env var if needed.

    IMPORTANT: Call this BEFORE writing any task or script to understand
    the target environment's rules and relationships.
    """
    ctx = _load_workspace_context(force=reload)

    if not ctx["found"]:
        return _json_response(
            {
                "status": "no_context",
                "message": (
                    "No RUNWHEN.md file found. Create a RUNWHEN.md in your project root "
                    "describing infrastructure conventions, database access rules, naming "
                    "patterns, and other constraints. See the MCP server docs for the "
                    "recommended format."
                ),
            }
        )

    return _json_response(
        {
            "status": "ok",
            "source": ctx["path"],
            "content": ctx["content"],
        }
    )


# ---------------------------------------------------------------------------
# Tool Builder Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_workspace_secrets(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
) -> str:
    """List available secret key names in a workspace.

    Returns the secret keys that can be referenced when running or committing
    scripts (e.g. "kubeconfig", "api-token"). These map environment variable
    names to workspace-stored secrets.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/secrets-keys")
    return _json_response(data)


@mcp.tool()
async def get_workspace_locations(
    workspace_name: str = Field(description="The workspace to query (e.g. 't-oncall')."),
) -> str:
    """List available runner locations for a workspace.

    Runner locations are where scripts execute. Returns location identifiers
    that can be used with run_script and commit_slx.
    """
    ws = await _resolve_workspace(workspace_name)
    locations = await _get_authorized_locations(ws)
    return _json_response(locations)


def _resolve_script(
    script: str | None,
    script_path: str | None,
    script_base64: str | None = None,
) -> str:
    """Return script content from inline text, base64, or a local file path.

    Exactly one of *script*, *script_path*, or *script_base64* must be provided.
    *script_base64* is standard base64 of UTF-8 text — use when MCP clients
    struggle to JSON-escape multiline scripts (e.g. ``def main():``).
    *script_path* reads the file on the MCP server host (Tool Builder / stdio).
    """
    has_script = script is not None and script != ""
    has_path = bool(script_path)
    has_b64 = bool(script_base64 and script_base64.strip())
    chosen = sum(1 for x in (has_script, has_path, has_b64) if x)
    if chosen != 1:
        raise ValueError("Provide exactly one of 'script', 'script_path', or 'script_base64'.")
    if script_base64:
        try:
            raw = base64.b64decode(script_base64.strip().encode("ascii"), validate=True)
            return raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                "Invalid script_base64: must be valid base64 encoding UTF-8 text."
            ) from exc
    if script_path:
        if MCP_TRANSPORT == "http":
            raise ValueError(
                "script_path is not supported in HTTP mode. "
                "Use 'script' (inline) or 'script_base64' instead."
            )
        path = os.path.expanduser(script_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Script file not found: {path}")
        with open(path) as f:
            return f.read()
    return script or ""


@mcp.tool()
async def validate_script(
    script: Annotated[
        str | None,
        Field(default=None, description="The full script source code (raw text)."),
    ] = None,
    script_path: Annotated[
        str | None,
        Field(
            default=None,
            description="Local file path to read the script from. "
            "Mutually exclusive with 'script' and 'script_base64'.",
        ),
    ] = None,
    script_base64: Annotated[
        str | None,
        Field(
            default=None,
            description="UTF-8 script as standard base64. Prefer when JSON-escaping "
            "multiline scripts is error-prone. Mutually exclusive with 'script'.",
        ),
    ] = None,
    interpreter: str = Field(default="bash", description="'bash' or 'python'."),
    task_type: str = Field(
        default="task", description="'task' (returns issues) or 'sli' (returns 0-1 metric)."
    ),
) -> str:
    """Validate a script against the RunWhen contract before running it.

    Checks that the script follows the required structure (main function,
    correct output format, etc.) and extracts referenced environment variables.

    Task scripts must return/write issues with keys: 'issue title',
    'issue description', 'issue severity' (1-4), 'issue next steps',
    and optionally 'issue observed at'.
    """
    try:
        script_resolved = _resolve_script(script, script_path, script_base64)
    except ValueError as exc:
        return _json_response({"valid": False, "error": str(exc), "warnings": []})
    warnings = _validate_script(script_resolved, interpreter, task_type)
    env_vars = _extract_env_vars(script_resolved, interpreter)

    result: dict[str, Any] = {
        "valid": len(warnings) == 0,
        "warnings": warnings,
        "detected_env_vars": env_vars,
        "interpreter": interpreter,
        "task_type": task_type,
    }

    if not warnings:
        result["message"] = "Script passes RunWhen contract validation."
    else:
        result["message"] = (
            f"Script has {len(warnings)} contract warning(s). "
            "Fix these before running or committing."
        )

    return _json_response(result)


@mcp.tool()
async def run_script(
    workspace_name: str = Field(description="The workspace to run in (e.g. 't-oncall')."),
    script: Annotated[
        str | None, Field(description="The full script source code (raw text).")
    ] = None,
    location: str = Field(default="", description="Runner location (use get_workspace_locations)."),
    interpreter: str = Field(default="bash", description="'bash' or 'python'."),
    run_type: str = Field(default="task", description="'task' or 'sli'."),
    env_vars: Annotated[
        dict[str, str] | None,
        Field(description="Environment variables (e.g. {'NAMESPACE': 'default'})."),
    ] = None,
    secret_vars: Annotated[
        dict[str, str] | None,
        Field(description="Secret mappings (e.g. {'kubeconfig': 'kubeconfig'})."),
    ] = None,
    script_path: Annotated[
        str | None,
        Field(
            description="Local file path to read the script from. Mutually exclusive with "
            "'script' and 'script_base64'."
        ),
    ] = None,
    script_base64: Annotated[
        str | None,
        Field(
            default=None,
            description="UTF-8 script as standard base64. Prefer when JSON-escaping multiline "
            "scripts is error-prone.",
        ),
    ] = None,
) -> str:
    """Execute a script on a RunWhen runner for testing.

    Sends the script to the workspace's runner at the specified location.
    Returns a run ID that can be used with get_run_status and get_run_output
    to monitor execution and retrieve results.

    The script must follow the RunWhen contract:
    - Python task: define main() returning List[Dict] with keys 'issue title',
      'issue description', 'issue severity' (1-4), 'issue next steps'.
    - Python SLI: define main() returning a float 0-1.
    - Bash task: define main() writing issue JSON array to FD 3 (>&3).
    - Bash SLI: define main() writing a metric float to FD 3.

    Use validate_script first to check compliance.
    """
    try:
        script = _resolve_script(script, script_path, script_base64)
    except (ValueError, FileNotFoundError) as exc:
        return _json_response({"error": str(exc)})

    ws = await _resolve_workspace(workspace_name)

    warnings = _validate_script(script, interpreter, run_type)
    if warnings:
        return _json_response(
            {
                "error": "Script validation failed",
                "warnings": warnings,
                "message": "Fix the warnings and try again. Use validate_script for details.",
            }
        )

    try:
        location = await _resolve_location(ws, location)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    body: dict[str, Any] = {
        "command": script,
        "location": location,
        "run_type": run_type,
        "interpreter": interpreter,
        "envVars": env_vars or {},
        "secretVars": secret_vars or {},
    }

    status_code, data = await _papi_post(f"/api/v3/workspaces/{ws}/author/run", body)
    return _json_response(data)


@mcp.tool()
async def get_run_status(
    run_id: str = Field(description="The run ID returned by run_script."),
    workspace_name: str = Field(description="The workspace the run belongs to (e.g. 't-oncall')."),
) -> str:
    """Check the status of a script run.

    Poll this after run_script to check if execution has completed.
    Status values: RUNNING, SUCCEEDED, FAILED.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/status")
    return _json_response(data)


@mcp.tool()
async def get_run_output(
    run_id: str = Field(description="The run ID returned by run_script."),
    workspace_name: str = Field(description="The workspace the run belongs to (e.g. 't-oncall')."),
    fetch_logs: bool = Field(default=True, description="Download and parse artifact contents."),
) -> str:
    """Get the output artifacts from a completed script run.

    Returns parsed, human-readable results including:
    - issues: list of issues found by the script (title, severity, details, nextSteps)
    - stdout: script stdout output
    - stderr: script stderr output
    - status: run status (SUCCEEDED, FAILED, RUNNING)
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/output")

    if not fetch_logs or not isinstance(data, dict):
        return _json_response(data)

    parsed = await _fetch_and_parse_artifacts(data)
    result = {
        "runId": run_id,
        "status": data.get("status", "UNKNOWN"),
        "issues": parsed["issues"],
        "stdout": parsed["stdout"],
        "stderr": parsed["stderr"],
        "report": parsed["report"],
    }
    return _json_response(result)


@mcp.tool()
async def run_script_and_wait(
    workspace_name: str = Field(description="The workspace to run in (e.g. 't-oncall')."),
    script: Annotated[
        str | None, Field(description="The full script source code (raw text).")
    ] = None,
    location: str = Field(default="", description="Runner location (use get_workspace_locations)."),
    interpreter: str = Field(default="bash", description="'bash' or 'python'."),
    run_type: str = Field(default="task", description="'task' or 'sli'."),
    env_vars: Annotated[
        dict[str, str] | None, Field(description="Environment variables for the script.")
    ] = None,
    secret_vars: Annotated[
        dict[str, str] | None,
        Field(description="Secret mappings (env var name to workspace secret key)."),
    ] = None,
    script_path: Annotated[
        str | None,
        Field(
            description="Local file path to read the script from. Mutually exclusive with "
            "'script' and 'script_base64'."
        ),
    ] = None,
    script_base64: Annotated[
        str | None,
        Field(
            default=None,
            description="UTF-8 script as standard base64. Prefer when JSON-escaping multiline "
            "scripts is error-prone.",
        ),
    ] = None,
) -> str:
    """Execute a script and wait for results (combines run + poll + output).

    This is a convenience tool that runs a script, polls until completion,
    and returns the full output — all in one call. Use this instead of
    calling run_script + get_run_status + get_run_output separately.

    The script must follow the RunWhen contract:
    - Python task: define main() returning List[Dict] with keys 'issue title',
      'issue description', 'issue severity' (1-4), 'issue next steps'.
    - Python SLI: define main() returning a float 0-1.
    - Bash task: define main() writing issue JSON array to FD 3 (>&3).
    - Bash SLI: define main() writing a metric float to FD 3.

    Secret vars are injected as env vars pointing to file paths on the runner.
    For kubeconfig: set KUBECONFIG = os.environ["kubeconfig"].
    """
    try:
        script = _resolve_script(script, script_path, script_base64)
    except (ValueError, FileNotFoundError) as exc:
        return _json_response({"error": str(exc)})

    ws = await _resolve_workspace(workspace_name)

    warnings = _validate_script(script, interpreter, run_type)
    if warnings:
        return _json_response(
            {
                "error": "Script validation failed",
                "warnings": warnings,
            }
        )

    try:
        location = await _resolve_location(ws, location)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    body: dict[str, Any] = {
        "command": script,
        "location": location,
        "run_type": run_type,
        "interpreter": interpreter,
        "envVars": env_vars or {},
        "secretVars": secret_vars or {},
    }

    _, run_data = await _papi_post(f"/api/v3/workspaces/{ws}/author/run", body)

    run_id = run_data.get("runId")
    if not run_id:
        return _json_response({"error": "No runId in response", "response": run_data})

    elapsed = 0
    status = "RUNNING"
    while status == "RUNNING" and elapsed < MAX_POLL_DURATION_S:
        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S
        status_data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/status")
        status = status_data.get("status", "UNKNOWN")

    await asyncio.sleep(ARTIFACT_SETTLE_DELAY_S)

    output_data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/output")

    parsed: dict[str, Any] = {"issues": [], "stdout": "", "stderr": "", "report": ""}
    if isinstance(output_data, dict):
        parsed = await _fetch_and_parse_artifacts(output_data)
        if not parsed["stdout"] and not parsed["issues"] and output_data.get("artifacts"):
            await asyncio.sleep(3)
            output_data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/output")
            if isinstance(output_data, dict):
                parsed = await _fetch_and_parse_artifacts(output_data)

    result = {
        "runId": run_id,
        "finalStatus": status,
        "elapsedSeconds": elapsed,
        "issues": parsed["issues"],
        "stdout": parsed["stdout"],
        "stderr": parsed["stderr"],
        "report": parsed["report"],
    }
    return _json_response(result)


# ---------------------------------------------------------------------------
# Run Existing SLX
# ---------------------------------------------------------------------------

SLX_RUN_MAX_POLL_S = 300
SLX_RUN_POLL_INTERVAL_S = 5


@mcp.tool()
async def run_slx(
    slx_name: str = Field(description="The SLX short name (e.g. 'k8s-pod-health')."),
    workspace_name: str = Field(description="The workspace (e.g. 't-oncall')."),
    task_titles: str = Field(
        default="*", description="Tasks to run: '*' for all, or '||'-separated titles."
    ),
) -> str:
    """Run an existing SLX's runbook tasks on the workspace runner.

    This triggers execution of a previously committed SLX (not an ad-hoc script).
    Use this when you want to run a health check, troubleshooting task, or
    automation that already exists in the workspace.

    IMPORTANT: This is different from run_script / run_script_and_wait, which
    execute ad-hoc scripts. Use run_slx to trigger SLXs that are already
    committed and configured in the workspace.

    NOTE: workspace_chat CANNOT run tasks directly — it can only search for
    and describe them. Use this tool to actually execute an SLX.

    The tool creates a RunRequest, starts it, polls until completion, and
    returns the results including pass/fail status and any issues found.
    """
    ws = await _resolve_workspace(workspace_name)

    # Step 1: Create a staged RunRequest
    create_body: dict[str, Any] = {
        "task_titles": task_titles,
        "memo": {"source": "mcp-tool", "tool": "run_slx"},
    }
    try:
        status_code, create_data = await _papi_post(
            f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook/runs",
            create_body,
        )
    except ValueError as exc:
        return _json_response({"error": f"Failed to create RunRequest: {exc}"})

    run_request_id = create_data.get("id")
    if not run_request_id:
        return _json_response({"error": "No RunRequest ID in response", "response": create_data})

    # Step 2: Start the RunRequest (submits to runner)
    try:
        _, start_data = await _papi_post(
            f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook/runs/{run_request_id}/start",
            {},
        )
    except ValueError as exc:
        return _json_response(
            {"error": f"Failed to start RunRequest: {exc}", "run_request_id": run_request_id}
        )

    # Step 3: Poll until completion
    elapsed = 0
    run_status = "running"
    run_data: dict[str, Any] = {}
    while run_status not in ("completed", "failed") and elapsed < SLX_RUN_MAX_POLL_S:
        await asyncio.sleep(SLX_RUN_POLL_INTERVAL_S)
        elapsed += SLX_RUN_POLL_INTERVAL_S
        try:
            run_data = await _papi_get(
                f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook/runs/{run_request_id}"
            )
            is_completed = run_data.get("isCompleted") or run_data.get("is_completed")
            response_time = run_data.get("responseTime") or run_data.get("response_time")
            if is_completed or response_time:
                run_status = "completed"
        except ValueError:
            pass

    if run_status != "completed":
        return _json_response(
            {
                "status": "timeout",
                "run_request_id": run_request_id,
                "elapsed_seconds": elapsed,
                "message": f"RunRequest did not complete within {SLX_RUN_MAX_POLL_S}s. "
                "It may still be running — check back later with get_run_sessions.",
                "last_state": run_data,
            }
        )

    # Step 4: Fetch output
    try:
        output_data = await _papi_get(
            f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook/runs/{run_request_id}/output"
        )
    except ValueError:
        output_data = {}

    result: dict[str, Any] = {
        "status": "completed",
        "slx_name": slx_name,
        "workspace": ws,
        "run_request_id": run_request_id,
        "elapsed_seconds": elapsed,
        "passed_titles": run_data.get("passedTitles") or run_data.get("passed_titles", ""),
        "failed_titles": run_data.get("failedTitles") or run_data.get("failed_titles", ""),
        "skipped_titles": run_data.get("skippedTitles") or run_data.get("skipped_titles", ""),
        "output": output_data,
    }
    return _json_response(result)


@mcp.tool()
async def commit_slx(
    slx_name: str = Field(
        description="Short SLX name (lowercase-kebab-case, e.g. 'k8s-pod-health')."
    ),
    alias: str = Field(description="Human-readable display name (e.g. 'Pod Health Check')."),
    statement: str = Field(description="SLX statement (e.g. 'All pods should be running')."),
    workspace_name: str = Field(description="The workspace to commit to (e.g. 't-oncall')."),
    script: Annotated[
        str | None, Field(description="The full script source code (not base64).")
    ] = None,
    task_title: str = Field(default="", description="Human-readable task title."),
    location: str = Field(default="", description="Runner location (use get_workspace_locations)."),
    interpreter: str = Field(default="bash", description="'bash' or 'python'."),
    task_type: str = Field(default="task", description="'task' (runbook) or 'sli' (indicator)."),
    owners: Annotated[
        list[str] | None, Field(description="Owner emails (defaults to current user).")
    ] = None,
    branch: str = Field(default="main", description="Git branch to commit to."),
    env_vars: Annotated[
        dict[str, str] | None, Field(description="Environment variables baked into the SLX config.")
    ] = None,
    secret_vars: Annotated[
        dict[str, str] | None, Field(description="Secret mappings baked into the SLX config.")
    ] = None,
    tags: Annotated[
        list[dict[str, str]] | None, Field(description="Resource tags ({name, value} dicts).")
    ] = None,
    interval_seconds: int = Field(
        default=300, description="For SLIs, how often to run in seconds."
    ),
    commit_message: Annotated[str | None, Field(description="Custom commit message.")] = None,
    sli_script: Annotated[
        str | None, Field(description="Optional SLI script (returns float 0-1).")
    ] = None,
    sli_interpreter: Annotated[
        str | None, Field(description="Interpreter for the SLI script.")
    ] = None,
    sli_interval_seconds: int = Field(
        default=300, description="How often the SLI runs in seconds."
    ),
    cron_schedule: Annotated[
        str | None, Field(description="Cron expression to schedule the task (e.g. '0 */2 * * *').")
    ] = None,
    image_url: Annotated[str | None, Field(description="Icon URL for the SLX.")] = None,
    access: str = Field(default="read-write", description="'read-write' or 'read-only'."),
    data: str = Field(
        default="logs-bulk", description="'logs-bulk', 'config', or 'logs-stacktrace'."
    ),
    resource_path: Annotated[
        str | None, Field(description="Resource path for search indexing.")
    ] = None,
    hierarchy: Annotated[
        list[str] | None, Field(description="Tag names for hierarchical grouping.")
    ] = None,
    codebundle_ref: Annotated[
        str | None, Field(description="Git ref for the codebundle (auto-resolved if omitted).")
    ] = None,
    script_path: Annotated[
        str | None,
        Field(
            description="Local file path for main script. Mutually exclusive with 'script' "
            "and 'script_base64'."
        ),
    ] = None,
    script_base64: Annotated[
        str | None,
        Field(
            default=None,
            description="UTF-8 main script as standard base64. Mutually exclusive with "
            "'script' and 'script_path'.",
        ),
    ] = None,
    sli_script_path: Annotated[
        str | None,
        Field(
            description="Local file path for SLI script. Mutually exclusive with "
            "'sli_script' and 'sli_script_base64'."
        ),
    ] = None,
    sli_script_base64: Annotated[
        str | None,
        Field(
            default=None,
            description="UTF-8 SLI script as standard base64. Mutually exclusive with "
            "'sli_script' and 'sli_script_path'.",
        ),
    ] = None,
) -> str:
    """Commit a tested script as an SLX to the workspace Git repo.

    Creates a new SLX with the script as a Task (runbook) and/or SLI.
    The script should already be tested via run_script or run_script_and_wait.

    This writes slx.yaml + runbook.yaml (for tasks) or slx.yaml + sli.yaml
    (for SLIs) to the workspace repository.

    To commit BOTH a task AND an SLI on the same SLX, use one of these approaches:

    1. Custom SLI script: set task_type="task" and provide sli_script with
       a script that returns a 0-1 metric. Generates both runbook.yaml and sli.yaml.

    2. Cron-scheduler SLI: set task_type="task" and provide cron_schedule with
       a cron expression (e.g. "0 */2 * * *"). The SLI will trigger the task's
       runbook on that schedule.
    """
    try:
        _validate_slx_name(slx_name)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    try:
        script = _resolve_script(script, script_path, script_base64)
    except (ValueError, FileNotFoundError) as exc:
        return _json_response({"error": str(exc)})

    if sli_script_path or sli_script is not None or sli_script_base64:
        try:
            sli_script = _resolve_script(sli_script, sli_script_path, sli_script_base64)
        except (ValueError, FileNotFoundError) as exc:
            return _json_response({"error": f"SLI script: {exc}"})

    ws = await _resolve_workspace(workspace_name)

    try:
        location = await _resolve_location(ws, location)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    script_b64 = base64.b64encode(script.encode()).decode()

    if sli_script and cron_schedule:
        return _json_response(
            {
                "error": "Cannot specify both sli_script and cron_schedule. "
                "Use sli_script for a custom SLI metric, or cron_schedule "
                "to trigger the runbook on a schedule.",
            }
        )

    if access not in VALID_ACCESS_TAGS:
        valid = ", ".join(VALID_ACCESS_TAGS)
        return _json_response({"error": f"Invalid access tag '{access}'. Must be one of: {valid}"})
    if data not in VALID_DATA_TAGS:
        valid = ", ".join(VALID_DATA_TAGS)
        return _json_response({"error": f"Invalid data tag '{data}'. Must be one of: {valid}"})

    if owners is None:
        owners = [await _get_user_email()]

    if not codebundle_ref:
        codebundle_ref = await _get_codebundle_ref(ws)

    additional_context: dict[str, Any] | None = None
    if resource_path or hierarchy:
        additional_context = {}
        if resource_path:
            additional_context["resourcePath"] = resource_path
        if hierarchy:
            additional_context["hierarchy"] = hierarchy

    slx_yaml = _build_slx_yaml(
        workspace=ws,
        slx_name=slx_name,
        alias=alias,
        statement=statement,
        owners=owners,
        tags=tags,
        image_url=image_url,
        access=access,
        data=data,
        additional_context=additional_context,
    )

    files: dict[str, str] = {"slx.yaml": slx_yaml}
    committed_types: list[str] = []

    if task_type == "task":
        files["runbook.yaml"] = _build_runbook_yaml(
            workspace=ws,
            slx_name=slx_name,
            script_b64=script_b64,
            interpreter=interpreter,
            task_title=task_title,
            location=location,
            env_vars=env_vars,
            secret_vars=secret_vars,
            codebundle_ref=codebundle_ref,
        )
        committed_types.append("task")

        if sli_script:
            sli_b64 = base64.b64encode(sli_script.encode()).decode()
            sli_interp = sli_interpreter or interpreter
            files["sli.yaml"] = _build_sli_yaml(
                workspace=ws,
                slx_name=slx_name,
                script_b64=sli_b64,
                interpreter=sli_interp,
                location=location,
                interval_seconds=sli_interval_seconds,
                env_vars=env_vars,
                secret_vars=secret_vars,
                codebundle_ref=codebundle_ref,
            )
            committed_types.append("sli (custom script)")

        elif cron_schedule:
            files["sli.yaml"] = _build_cron_sli_yaml(
                workspace=ws,
                slx_name=slx_name,
                location=location,
                cron_schedule=cron_schedule,
                interval_seconds=sli_interval_seconds,
            )
            committed_types.append("sli (cron-scheduler)")

    elif task_type == "sli":
        files["sli.yaml"] = _build_sli_yaml(
            workspace=ws,
            slx_name=slx_name,
            script_b64=script_b64,
            interpreter=interpreter,
            location=location,
            interval_seconds=interval_seconds,
            env_vars=env_vars,
            secret_vars=secret_vars,
            codebundle_ref=codebundle_ref,
        )
        committed_types.append("sli")

    type_label = " + ".join(committed_types)
    if not commit_message:
        commit_message = f"Add {type_label} SLX: {alias}"

    body = {
        "commit_msg": commit_message,
        "files": files,
    }

    status_code, data = await _papi_post(
        f"/api/v3/workspaces/{ws}/branches/{branch}/slxs/{slx_name}",
        body,
    )

    success = status_code in (200, 201)
    result = {
        "status": "committed" if success else f"error_{status_code}",
        "slx_name": slx_name,
        "workspace": ws,
        "branch": branch,
        "codebundle_ref": codebundle_ref,
        "committed_files": list(files.keys()),
        "committed_types": type_label,
        "response": data,
    }
    return _json_response(result)


@mcp.tool()
async def delete_slx(
    slx_name: str = Field(description="Short name of the SLX to delete (e.g. 'k8s-pod-health')."),
    workspace_name: str = Field(description="The workspace to delete from (e.g. 't-oncall')."),
    branch: str = Field(default="main", description="Git branch to delete from."),
    commit_message: Annotated[str | None, Field(description="Custom commit message.")] = None,
) -> str:
    """Delete an SLX from the workspace Git repo.

    Removes the SLX directory (slx.yaml, runbook.yaml, sli.yaml) from the
    workspace configuration repository.
    """
    try:
        _validate_slx_name(slx_name)
    except ValueError as exc:
        return _json_response({"error": str(exc)})

    ws = await _resolve_workspace(workspace_name)

    if not commit_message:
        commit_message = f"Remove SLX: {slx_name}"

    try:
        status_code, data = await _papi_delete(
            f"/api/v3/workspaces/{ws}/branches/{branch}/slxs/{slx_name}",
        )
    except (ValueError, httpx.HTTPStatusError) as exc:
        return _json_response({"error": f"Failed to delete SLX: {exc}"})

    result = {
        "status": "deleted" if status_code in (200, 204) else f"status_{status_code}",
        "slx_name": slx_name,
        "workspace": ws,
        "branch": branch,
        "response": data,
    }
    return _json_response(result)


_TOOL_FUNCTIONS = [
    workspace_chat,
    list_workspaces,
    get_workspace_chat_config,
    list_chat_rules,
    get_chat_rule,
    create_chat_rule,
    update_chat_rule,
    list_chat_commands,
    get_chat_command,
    create_chat_command,
    update_chat_command,
    get_workspace_issues,
    get_workspace_slxs,
    get_run_sessions,
    get_workspace_config_index,
    get_issue_details,
    get_slx_runbook,
    search_workspace,
    list_knowledge_base_articles,
    get_knowledge_base_article,
    create_knowledge_base_article,
    update_knowledge_base_article,
    delete_knowledge_base_article,
    search_registry,
    get_registry_codebundle,
    deploy_registry_codebundle,
    get_workspace_context,
    get_workspace_secrets,
    get_workspace_locations,
    validate_script,
    run_script,
    get_run_status,
    get_run_output,
    run_script_and_wait,
    run_slx,
    commit_slx,
    delete_slx,
]


def _make_workspace_auth_check(tool_name: str) -> Any:
    """Create a FastMCP AuthCheck for a tool that validates workspace access.

    Reads the workspace_name from the tool call arguments (if present),
    resolves the user's role from PAPI, and compares against the minimum
    required role for the tool.

    Falls back to simple authentication check for tools without a
    workspace_name parameter (e.g. validate_script, get_workspace_context).
    """
    from fastmcp.server.auth import AuthContext

    from runwhen_platform_mcp.authorization import (
        WorkspaceRole,
        get_user_workspace_role,
        minimum_role_for_tool,
    )

    async def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False

        token_str = ctx.token.token

        papi_oauth_client = os.environ.get("MCP_PAPI_OAUTH_CLIENT_ID", "")
        is_papi_oidc_token = papi_oauth_client and ctx.token.client_id == papi_oauth_client
        is_papi_jwt = ctx.token.client_id == "papi-jwt"
        is_pat = ctx.token.client_id == "runwhen-pat"
        is_papi_upstream = bool(
            ctx.token.claims
            and ctx.token.claims.get("type") in ("access", "refresh")
            and ctx.token.claims.get("iss")
        )

        if (
            not is_pat
            and not is_papi_oidc_token
            and not is_papi_jwt
            and not is_papi_upstream
            and PAPI_URL
        ):
            from runwhen_platform_mcp.auth import exchange_auth0_for_papi

            papi_token = await exchange_auth0_for_papi(token_str, PAPI_URL)
            if papi_token is None:
                return False
            token_str = papi_token

        _request_token.set(token_str)

        if not PAPI_URL:
            return True

        required_role = minimum_role_for_tool(tool_name)
        if required_role == WorkspaceRole.READ_ONLY:
            return True

        workspace = None
        if hasattr(ctx, "arguments") and isinstance(ctx.arguments, dict):
            workspace = ctx.arguments.get("workspace_name")

        if not workspace:
            return True

        user_role = await get_user_workspace_role(PAPI_URL, token_str, workspace)
        if user_role is None:
            return False
        return user_role >= required_role

    return check


def _build_http_server() -> FastMCP:
    """Build an HTTP-mode MCP server with authentication and health checks.

    Creates a new FastMCP instance with auth and re-registers all tool
    functions with workspace-level authorization checks.
    """
    from runwhen_platform_mcp.auth import build_auth_provider
    from runwhen_platform_mcp.consent_ui import patch_fastmcp_consent_ui

    patch_fastmcp_consent_ui()
    auth = build_auth_provider()

    http_mcp = FastMCP(
        _build_server_name(),
        instructions=_build_server_instructions(),
        auth=auth,
    )

    from fastmcp.tools.function_tool import FunctionTool

    for fn in _TOOL_FUNCTIONS:
        auth_check = _make_workspace_auth_check(fn.__name__)
        tool = FunctionTool.from_function(fn, auth=auth_check)  # type: ignore[arg-type]
        http_mcp.add_tool(tool)

    @http_mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Any) -> Any:
        from starlette.responses import JSONResponse

        healthy = bool(PAPI_URL)
        status_code = 200 if healthy else 503
        return JSONResponse(
            {"status": "healthy" if healthy else "unhealthy", "papi_url": PAPI_URL or "not set"},
            status_code=status_code,
        )

    @http_mcp.custom_route("/livez", methods=["GET"])
    async def liveness(request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "alive"})

    return http_mcp


def main() -> None:
    """Entry point for the runwhen-platform-mcp console script.

    Supports two transport modes controlled by MCP_TRANSPORT env var:
      - "stdio" (default): Local subprocess mode, auth via RUNWHEN_TOKEN
      - "http": Remote Streamable HTTP mode, auth via Bearer token

    HTTP mode env vars:
      - MCP_HOST: Bind address (default: 0.0.0.0)
      - MCP_PORT: Listen port (default: 8000)
      - FASTMCP_STATELESS_HTTP: Set to "true" for horizontal scaling
    """
    if MCP_TRANSPORT == "http":
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))
        stateless = os.environ.get("FASTMCP_STATELESS_HTTP", "true").lower() == "true"

        http_mcp = _build_http_server()
        http_mcp.run(transport="http", host=host, port=port, stateless_http=stateless)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
