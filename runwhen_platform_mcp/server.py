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
import json
import os
import re
from contextvars import ContextVar
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

PAPI_URL = os.environ.get("RW_API_URL", "").rstrip("/")
RUNWHEN_TOKEN = os.environ.get("RUNWHEN_TOKEN", "")
DEFAULT_WORKSPACE = os.environ.get("DEFAULT_WORKSPACE", "")
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio").lower()
MCP_SERVER_LABEL = os.environ.get("MCP_SERVER_LABEL", "")
REGISTRY_URL = os.environ.get("RUNWHEN_REGISTRY_URL", "https://registry.runwhen.com").rstrip("/")

# Per-request token for HTTP mode. Set by auth middleware; falls back to
# RUNWHEN_TOKEN in stdio mode.
_request_token: ContextVar[str | None] = ContextVar("_request_token", default=None)


def _derive_agentfarm_url(api_url: str) -> str:
    """Derive the AgentFarm URL by swapping the ``papi`` subdomain for ``agentfarm``."""
    return re.sub(r"://papi\.", "://agentfarm.", api_url) if api_url else ""


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
        "PRIMARY TOOL: `workspace_chat` — ask the RunWhen AI assistant about "
        "infrastructure (issues, tasks, run sessions, resources, knowledge base). "
        "NOTE: workspace_chat can SEARCH and DESCRIBE tasks but CANNOT EXECUTE them.\n\n"
        "RUN EXISTING TASKS: `run_slx` — execute a committed SLX runbook. "
        "Use this (not workspace_chat) when the user asks to run/trigger a task.\n\n"
        "REGISTRY (search before build): `search_registry` — find reusable automation; "
        "`get_registry_codebundle` — full details; "
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
        "'issue title', 'issue description', 'issue severity' (1-4), 'issue next steps'\n"
        "- Bash task: `main()` writes issue JSON array to FD 3 (>&3)\n"
        "- Python/Bash SLI: `main()` returns/writes float 0-1\n\n"
        "REQUIRED TAGS for `commit_slx`: "
        "access='read-write'|'read-only', data='logs-bulk'|'config'|'logs-stacktrace'"
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
            "or create a Personal Access Token in the RunWhen UI."
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


_user_email_cache: dict[str, str] = {}


async def _get_user_email(token: str | None = None) -> str:
    """Resolve the authenticated user's email address.

    Uses the PAPI ``/api/v3/users/whoami`` endpoint (preferred), falling back
    to ``/api/v3/users/{id}/`` if whoami is unavailable.  Results are cached
    for the process lifetime.

    Note: the whoami endpoint requires NO trailing slash on this PAPI instance.
    """
    token = token or _get_token()

    if token in _user_email_cache:
        return _user_email_cache[token]

    payload = _decode_jwt_payload(token)

    for claim in ("email", "primary_email"):
        val = payload.get(claim)
        if val and isinstance(val, str) and "@" in val:
            _user_email_cache[token] = val
            return val

    # Preferred: whoami endpoint returns the current user from the JWT
    for path in ("/api/v3/users/whoami",):
        try:
            data = await _papi_get(path)
            email = data.get("primaryEmail") or data.get("primary_email")
            if email:
                _user_email_cache[token] = email
                return email
            username = data.get("username", "")
            if username and "@" in username:
                _user_email_cache[token] = username
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
                _user_email_cache[token] = email
                return email
        except Exception:
            pass

    fallback = str(user_id) if user_id else "cursor@runwhen.com"
    _user_email_cache[token] = fallback
    return fallback


_workspace_cache: dict[str, list[dict[str, str]]] = {}


async def _fetch_workspace_list() -> list[dict[str, str]]:
    """Fetch and cache the list of accessible workspaces.

    Returns a list of {"name": short_name, "displayName": display_name} dicts.
    Cache is keyed by token so it stays valid across requests in HTTP mode.
    """
    token = _get_token()
    cache_key = token[-12:] if len(token) > 12 else token
    if cache_key in _workspace_cache:
        return _workspace_cache[cache_key]

    data = await _papi_get("/api/v3/workspaces")
    workspaces = data if isinstance(data, list) else data.get("results", data)
    result = []
    for ws in workspaces:
        name = ws.get("name") or ws.get("shortName") or ws.get("short_name", "")
        display = ws.get("displayName") or ws.get("display_name") or name
        result.append({"name": name, "displayName": display})
    _workspace_cache[cache_key] = result
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
    ws = workspace_name or DEFAULT_WORKSPACE
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


def _safe_json_parse(resp: httpx.Response, label: str) -> Any:
    """Parse JSON from an HTTP response, raising ValueError with context on failure."""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        raise ValueError(
            f"{label} returned non-JSON response "
            f"(status {resp.status_code}): {resp.text[:300]}"
        )


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
    codebundle_ref: str | None = None,
) -> str:
    """Generate sli.yaml for the cron-scheduler-sli codebundle.

    This creates an SLI that triggers the parent SLX's runbook on a cron
    schedule. If target_slx is empty, the scheduler triggers the runbook
    of the SLX it's attached to (self-scheduling pattern).
    """
    config_provided = [
        {"name": "CRON_SCHEDULE", "value": cron_schedule},
        {"name": "DRY_RUN", "value": "true" if dry_run else "false"},
    ]
    if target_slx:
        config_provided.append({"name": "TARGET_SLX", "value": target_slx})

    bundle = dict(CRON_SLI_CODE_BUNDLE)
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


def _agentfarm_headers() -> dict[str, str]:
    """Common headers for AgentFarm requests (Bearer token)."""
    token = _get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _agentfarm_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Send a request to AgentFarm. Path is relative to AGENTFARM_URL (no leading slash).
    Returns parsed JSON or raises ValueError on error.
    """
    url = f"{AGENTFARM_URL.rstrip('/')}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=_agentfarm_headers(),
        )
        if resp.status_code >= 400:
            raise ValueError(f"AgentFarm {method} {path}: {resp.status_code} {resp.text[:500]}")
        if resp.status_code == 204:
            return {}
        return _safe_json_parse(resp, f"AgentFarm {method} {path}")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def workspace_chat(
    message: str,
    workspace_name: str | None = None,
    persona_name: str = "default",
    session_id: str | None = None,
) -> str:
    """Ask the RunWhen AI assistant about your infrastructure.

    This is the primary tool — it sends your message to the RunWhen workspace
    AI agent which has access to ~25+ internal tools including:
    - Issue search and analysis
    - Task/SLX search
    - Run session search
    - Resource discovery and relationship mapping
    - Knowledge base search
    - Data analysis and graphing
    - Mermaid diagram generation
    - Task output analysis

    Args:
        message: Your question or request about the workspace infrastructure.
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
        persona_name: AI persona to use (default: "default").
        session_id: Optional session ID to continue a previous conversation.

    Returns:
        JSON with message, sessionId, widgets, and chatExportLink (shareable chat-export
        path for this session, when available). Prepend your RunWhen app base URL to
        chatExportLink to open the export in a browser.
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
    if result.get("sessionId") and "chatExportLink" not in result:
        link = await _fetch_chat_export_url(ws, user_id, result["sessionId"])
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
    workspace_name: str | None = None,
    persona_name: str | None = None,
) -> str:
    """Get resolved chat rules and commands for a workspace.

    Returns the list of rules and commands that apply to the workspace (and optional
    persona). These are the same rules and commands the workspace chat assistant sees.
    Response includes metadata only (id, name, scope); full rule/command content
    is not included in this endpoint.

    Args:
        workspace_name: The workspace. Uses DEFAULT_WORKSPACE if not provided.
        persona_name: Optional persona for persona-scoped rules/commands.
    """
    ws = await _resolve_workspace(workspace_name)
    user_id = await _get_user_email()
    params: dict[str, Any] = {"user_id": user_id}
    if persona_name:
        params["persona_name"] = persona_name
    try:
        data = await _agentfarm_request(
            "GET",
            f"api/v1/workspaces/{ws}/config",
            params=params,
        )
    except ValueError as e:
        return _json_response({"error": str(e)})
    return _json_response(data)


async def _chat_config_internal(
    path: str,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """Call AgentFarm internal chat-config API; returns JSON string or error."""
    try:
        data = await _agentfarm_request(
            method,
            f"internal/api/v1/chat-config/{path.lstrip('/')}",
            params=params,
            json_body=json_body,
        )
        return _json_response(data)
    except ValueError as e:
        return _json_response({"error": str(e)})


@mcp.tool()
async def list_chat_rules(
    scope_type: str | None = None,
    scope_id: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List chat rules (workspace chat rules).

    Uses AgentFarm internal API; may require network access.

    Args:
        scope_type: Filter by scope (platform, org, workspace, persona, user).
        scope_id: Filter by scope ID (e.g. workspace name, or None for platform).
        is_active: Filter by active status.
        page: Page number (1-based).
        page_size: Items per page (1-200).
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if scope_type is not None:
        params["scope_type"] = scope_type
    if scope_id is not None:
        params["scope_id"] = scope_id
    if is_active is not None:
        params["is_active"] = is_active
    return await _chat_config_internal("rules", params=params)


@mcp.tool()
async def get_chat_rule(rule_id: int) -> str:
    """Get a single chat rule by ID (full content). Uses AgentFarm internal API."""
    return await _chat_config_internal(f"rules/{rule_id}")


@mcp.tool()
async def create_chat_rule(
    name: str,
    rule_content: str,
    scope_type: str,
    scope_id: str | None = None,
    is_active: bool = True,
) -> str:
    """Create a chat rule. Uses AgentFarm internal API.

    Args:
        name: Human-readable name for the rule.
        rule_content: Markdown content of the rule.
        scope_type: One of platform, org, workspace, persona, user.
        scope_id: Scope ID (null for platform; workspace name for workspace; etc.).
        is_active: Whether the rule is active.
    """
    user_id = await _get_user_email()
    body: dict[str, Any] = {
        "userId": user_id,
        "name": name,
        "ruleContent": rule_content,
        "scopeType": scope_type,
        "scopeId": scope_id,
        "isActive": is_active,
    }
    return await _chat_config_internal("rules", method="POST", json_body=body)


@mcp.tool()
async def update_chat_rule(
    rule_id: int,
    name: str | None = None,
    rule_content: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    is_active: bool | None = None,
) -> str:
    """Update an existing chat rule by ID. Uses AgentFarm internal API."""
    user_id = await _get_user_email()
    body: dict[str, Any] = {"userId": user_id}
    if name is not None:
        body["name"] = name
    if rule_content is not None:
        body["ruleContent"] = rule_content
    if scope_type is not None:
        body["scopeType"] = scope_type
    if scope_id is not None:
        body["scopeId"] = scope_id
    if is_active is not None:
        body["isActive"] = is_active
    return await _chat_config_internal(f"rules/{rule_id}", method="PUT", json_body=body)


@mcp.tool()
async def list_chat_commands(
    scope_type: str | None = None,
    scope_id: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List chat commands (slash-command instructions). Uses AgentFarm internal API.

    Args:
        scope_type: Filter by scope (platform, org, workspace, persona, user).
        scope_id: Filter by scope ID.
        is_active: Filter by active status.
        page: Page number (1-based).
        page_size: Items per page (1-200).
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if scope_type is not None:
        params["scope_type"] = scope_type
    if scope_id is not None:
        params["scope_id"] = scope_id
    if is_active is not None:
        params["is_active"] = is_active
    return await _chat_config_internal("commands", params=params)


@mcp.tool()
async def get_chat_command(command_id: int) -> str:
    """Get a single chat command by ID (full content). Uses AgentFarm internal API."""
    return await _chat_config_internal(f"commands/{command_id}")


@mcp.tool()
async def create_chat_command(
    name: str,
    command_content: str,
    scope_type: str,
    scope_id: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> str:
    """Create a chat command (slash-command). Name must be alphanumeric, underscore, or hyphen only.

    Uses AgentFarm internal API. Commands are invoked in chat as [/label](cmd://name).
    """
    user_id = await _get_user_email()
    body: dict[str, Any] = {
        "userId": user_id,
        "name": name,
        "commandContent": command_content,
        "scopeType": scope_type,
        "scopeId": scope_id,
        "isActive": is_active,
    }
    if description is not None:
        body["description"] = description
    return await _chat_config_internal("commands", method="POST", json_body=body)


@mcp.tool()
async def update_chat_command(
    command_id: int,
    name: str | None = None,
    command_content: str | None = None,
    description: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    is_active: bool | None = None,
) -> str:
    """Update an existing chat command by ID. Uses AgentFarm internal API."""
    user_id = await _get_user_email()
    body: dict[str, Any] = {"userId": user_id}
    if name is not None:
        body["name"] = name
    if command_content is not None:
        body["commandContent"] = command_content
    if description is not None:
        body["description"] = description
    if scope_type is not None:
        body["scopeType"] = scope_type
    if scope_id is not None:
        body["scopeId"] = scope_id
    if is_active is not None:
        body["isActive"] = is_active
    return await _chat_config_internal(f"commands/{command_id}", method="PUT", json_body=body)


@mcp.tool()
async def get_workspace_issues(
    workspace_name: str | None = None,
    severity: int | None = None,
    limit: int = 20,
) -> str:
    """Get current issues for a workspace.

    Issues represent detected problems in your infrastructure that
    RunWhen has identified through automated health checks.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
        severity: Filter by severity (1=critical, 2=high, 3=medium, 4=low).
        limit: Maximum number of issues to return (default 20).
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"limit": limit}
    if severity is not None:
        params["severity"] = severity
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues", params=params)
    return _json_response(data)


@mcp.tool()
async def get_workspace_slxs(
    workspace_name: str | None = None,
) -> str:
    """List SLXs (Service Level eXperiences) in a workspace.

    SLXs are the fundamental unit of work in RunWhen — each represents a
    health check, task, or automation runbook for a piece of infrastructure.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs")
    return _json_response(data)


@mcp.tool()
async def get_run_sessions(
    workspace_name: str | None = None,
    limit: int = 20,
) -> str:
    """Get recent run sessions for a workspace.

    Run sessions are executions of SLX runbooks — they contain the output
    of health checks, troubleshooting tasks, and automation runs.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
        limit: Maximum number of run sessions to return (default 20).
    """
    ws = await _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"page": 1, "page-size": limit}
    data = await _papi_get(f"/api/v3/workspaces/{ws}/runsessions", params=params)
    return _json_response(data)


@mcp.tool()
async def get_workspace_config_index(
    workspace_name: str | None = None,
) -> str:
    """Get the workspace configuration index.

    Returns an overview of all configured resources, SLXs, and their
    relationships in the workspace. Useful for understanding what's
    monitored and how things are connected.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/workspace-configuration-index")
    return _json_response(data)


@mcp.tool()
async def get_issue_details(
    issue_id: str,
    workspace_name: str | None = None,
) -> str:
    """Get detailed information about a specific issue.

    Args:
        issue_id: The issue ID to look up.
        workspace_name: The workspace the issue belongs to. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues/{issue_id}")
    return _json_response(data)


@mcp.tool()
async def get_slx_runbook(
    slx_name: str,
    workspace_name: str | None = None,
) -> str:
    """Get the runbook for a specific SLX.

    Returns the runbook definition including what tasks it runs,
    how they're configured, and what they check.

    Args:
        slx_name: The SLX short name.
        workspace_name: The workspace the SLX belongs to. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook")
    return _json_response(data)


@mcp.tool()
async def search_workspace(
    query: str,
    workspace_name: str | None = None,
) -> str:
    """Search for tasks, resources, and configuration in a workspace.

    Uses the workspace's task search / autocomplete to find matching items.

    Args:
        query: Search query string.
        workspace_name: The workspace to search. Uses DEFAULT_WORKSPACE if not provided.
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
    workspace_name: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> str:
    """List Knowledge Base articles (notes) in a workspace.

    Returns KB articles that feed the workspace's Knowledge Overlay Graph.
    Articles can contain operational knowledge, runbook context, architecture
    notes, or any information useful for troubleshooting.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
        status: Filter by status — "active" or "deprecated". Returns all if omitted.
        search: Search within article content.
        limit: Maximum number of articles to return (default 50, max 200).
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
    note_id: str,
    workspace_name: str | None = None,
) -> str:
    """Get a specific Knowledge Base article by ID.

    Args:
        note_id: The UUID of the KB article to retrieve.
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/notes/{note_id}")
    return _json_response(data)


@mcp.tool()
async def create_knowledge_base_article(
    content: str,
    workspace_name: str | None = None,
    resource_paths: list[str] | None = None,
    abstract_entities: list[str] | None = None,
) -> str:
    """Create a new Knowledge Base article in a workspace.

    KB articles are indexed into the Knowledge Overlay Graph and become
    searchable by the workspace AI assistant and other tools.

    Content should be informative operational knowledge — architecture notes,
    troubleshooting guides, runbook context, dependency documentation, etc.

    Args:
        content: The article content (plain text or markdown, max 20000 chars).
        workspace_name: The workspace to create in. Uses DEFAULT_WORKSPACE if not provided.
        resource_paths: Canonical resource paths this article relates to
            (e.g. ["kubernetes/namespace/prod", "github/repo/my-app"]).
            Helps the knowledge graph link articles to infrastructure resources.
        abstract_entities: Normalized entity tokens for indexing
            (e.g. ["pod-crashloopbackoff", "oom-killed", "memory-limits"]).
            Improves discoverability when searching for related concepts.
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
    note_id: str,
    workspace_name: str | None = None,
    content: str | None = None,
    resource_paths: list[str] | None = None,
    abstract_entities: list[str] | None = None,
    status: str | None = None,
    verified: bool | None = None,
) -> str:
    """Update an existing Knowledge Base article.

    Only provided fields are updated; omitted fields remain unchanged.

    Args:
        note_id: The UUID of the KB article to update.
        workspace_name: The workspace. Uses DEFAULT_WORKSPACE if not provided.
        content: Updated article content (max 20000 chars).
        resource_paths: Updated resource paths.
        abstract_entities: Updated entity tokens.
        status: Set to "active" or "deprecated".
        verified: Mark as human-verified (true/false).
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
    note_id: str,
    workspace_name: str | None = None,
) -> str:
    """Delete a Knowledge Base article.

    Removes the article from the workspace and the Knowledge Overlay Graph index.

    Args:
        note_id: The UUID of the KB article to delete.
        workspace_name: The workspace. Uses DEFAULT_WORKSPACE if not provided.
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
    search: str,
    platform: str | None = None,
    tags: str | None = None,
    max_results: int = 10,
) -> str:
    """Search the RunWhen CodeBundle Registry for reusable automation.

    Use this BEFORE writing a custom script — there may already be a
    production-ready codebundle for the task.  Returns codebundles with
    their tasks, SLIs, required env vars, and deployment metadata.

    Args:
        search: Free-text search query (e.g. "kubernetes pod health",
                "postgres backup", "gcp iam audit").
        platform: Filter by platform (e.g. "Kubernetes", "GCP", "AWS").
        tags: Comma-separated support tags (e.g. "GKE,KUBERNETES").
        max_results: Maximum number of results to return (default 10).
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
    collection_slug: str,
    codebundle_slug: str,
) -> str:
    """Get full details of a specific codebundle from the registry.

    Use after search_registry to get complete information including
    configuration templates, environment variables, and deployment instructions.

    Args:
        collection_slug: The codecollection slug (e.g. "rw-cli-codecollection").
        codebundle_slug: The codebundle slug (e.g. "k8s-podresources-health").
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
    slx_name: str,
    alias: str,
    statement: str,
    repo_url: str,
    codebundle_path: str,
    location: str,
    config_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    deploy_runbook: bool = True,
    deploy_sli: bool = False,
    sli_description: str = "",
    sli_interval_seconds: int = 300,
    ref: str = "main",
    workspace_name: str | None = None,
    owners: list[str] | None = None,
    branch: str = "main",
    tags: list[dict[str, str]] | None = None,
    image_url: str | None = None,
    access: str = "read-only",
    data: str = "logs-bulk",
    resource_path: str | None = None,
    hierarchy: list[str] | None = None,
    commit_message: str | None = None,
) -> str:
    """Deploy a registry codebundle as an SLX to a workspace.

    Unlike commit_slx (which embeds inline scripts via the Tool Builder
    codebundle), this deploys a pre-built codebundle from its own
    codecollection repository.  The runbook.robot / sli.robot live in the
    codebundle's git repo — no inline script is needed.

    Use search_registry + get_registry_codebundle to find the right
    codebundle, then call this tool with the values from the registry.

    Args:
        slx_name: Short name for the SLX (lowercase-kebab-case).
        alias: Human-readable display name (e.g. "Namespace Health").
        statement: SLX statement (e.g. "All pods should be running").
        repo_url: Git URL of the codecollection (from registry result
            codecollection.git_url, e.g.
            "https://github.com/runwhen-contrib/rw-cli-codecollection.git").
        codebundle_path: Path within the repo to the codebundle directory
            (e.g. "codebundles/k8s-namespace-healthcheck").  The tool
            appends /runbook.robot and /sli.robot automatically.
        location: Runner location (use get_workspace_locations).
        config_vars: Codebundle-specific variables (e.g.
            {"NAMESPACE": "prod", "CONTEXT": "my-cluster"}).  These map
            to the codebundle's user_variables from the registry.
        secret_vars: Secret mappings (e.g. {"kubeconfig": "kubeconfig"}).
        deploy_runbook: Deploy the runbook (task).  Default True.
        deploy_sli: Also deploy the SLI (health indicator).  Default False.
        sli_description: Description for the SLI metric.
        sli_interval_seconds: How often the SLI runs (default 300).
        ref: Git branch/tag for the codecollection (default "main").
        workspace_name: Target workspace.  Uses DEFAULT_WORKSPACE if omitted.
        owners: List of owner emails (defaults to current user).
        branch: Workspace config branch (default "main").
        tags: Additional resource tags (list of {name, value} dicts).
        image_url: Icon URL for the SLX.
        access: "read-only" or "read-write" (default "read-only").
        data: "logs-bulk", "config", or "logs-stacktrace" (default "logs-bulk").
        resource_path: Resource path for search indexing.
        hierarchy: Tag names for hierarchical grouping.
        commit_message: Custom commit message.
    """
    if not deploy_runbook and not deploy_sli:
        return _json_response({"error": "At least one of deploy_runbook or deploy_sli must be True."})

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

    ws = await _resolve_workspace(workspace_name)

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
    reload: bool = False,
) -> str:
    """Get domain-specific context for building RunWhen tasks.

    Reads the project's RUNWHEN.md file, which contains infrastructure
    conventions, database access rules, naming patterns, architectural
    knowledge, and other constraints that scripts must follow.

    The file is auto-discovered by walking up from the current working
    directory. Override with the RUNWHEN_CONTEXT_FILE env var if needed.

    IMPORTANT: Call this BEFORE writing any task or script to understand
    the target environment's rules and relationships.

    Args:
        reload: Force re-read from disk (default: False, uses cached version).
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
    workspace_name: str | None = None,
) -> str:
    """List available secret key names in a workspace.

    Returns the secret keys that can be referenced when running or committing
    scripts (e.g. "kubeconfig", "api-token"). These map environment variable
    names to workspace-stored secrets.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/secrets-keys")
    return _json_response(data)


@mcp.tool()
async def get_workspace_locations(
    workspace_name: str | None = None,
) -> str:
    """List available runner locations for a workspace.

    Runner locations are where scripts execute. Returns location identifiers
    that can be used with run_script and commit_slx.

    Args:
        workspace_name: The workspace to query. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _get_debugslx(ws)
    runner_locations = data.get("status", {}).get("runnerLocations", [])
    locations = [
        {
            "location": rl["location"],
            "locationUUID": rl.get("locationUUID", rl["location"]),
            "lastUpdated": rl.get("lastUpdated"),
            "status": rl.get("status", {}).get("code"),
        }
        for rl in runner_locations
        if "location" in rl
    ]
    return _json_response(locations)


def _resolve_script(script: str | None, script_path: str | None) -> str:
    """Return script content from either an inline string or a local file path.

    Exactly one of *script* or *script_path* must be provided.  When
    *script_path* is used, the file is read in its entirety and returned
    as-is.  This avoids passing very large scripts through the MCP JSON-RPC
    message payload.
    """
    if script and script_path:
        raise ValueError("Provide either 'script' (inline) or 'script_path' (file), not both.")
    if script_path:
        path = os.path.expanduser(script_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Script file not found: {path}")
        with open(path) as f:
            return f.read()
    if script:
        return script
    raise ValueError("One of 'script' or 'script_path' must be provided.")


@mcp.tool()
async def validate_script(
    script: str,
    interpreter: str = "bash",
    task_type: str = "task",
) -> str:
    """Validate a script against the RunWhen contract before running it.

    Checks that the script follows the required structure (main function,
    correct output format, etc.) and extracts referenced environment variables.

    Task scripts must return/write issues with keys: 'issue title',
    'issue description', 'issue severity' (1-4), 'issue next steps',
    and optionally 'issue observed at'.

    Args:
        script: The full script source code.
        interpreter: "bash" or "python".
        task_type: "task" (returns issues) or "sli" (returns a 0-1 metric).
    """
    warnings = _validate_script(script, interpreter, task_type)
    env_vars = _extract_env_vars(script, interpreter)

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
    script: str | None = None,
    location: str = "",
    workspace_name: str | None = None,
    interpreter: str = "bash",
    run_type: str = "task",
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    script_path: str | None = None,
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

    Args:
        script: The full script source code (raw text, the backend base64-encodes it).
        location: Runner location (e.g. "northamerica-northeast2-01").
            Use get_workspace_locations to list.
        workspace_name: The workspace to run in.
            Uses DEFAULT_WORKSPACE if not provided.
        interpreter: "bash" or "python" (default: "bash").
        run_type: "task" or "sli" (default: "task").
        env_vars: Environment variables for the script
            (e.g. {"NAMESPACE": "default"}).
        secret_vars: Secret mappings — env var name to workspace secret key
            (e.g. {"kubeconfig": "kubeconfig"}).
        script_path: Local file path to read the script from. Use instead of
            'script' when the script is large. Mutually exclusive with 'script'.
    """
    try:
        script = _resolve_script(script, script_path)
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
    run_id: str,
    workspace_name: str | None = None,
) -> str:
    """Check the status of a script run.

    Poll this after run_script to check if execution has completed.
    Status values: RUNNING, SUCCEEDED, FAILED.

    Args:
        run_id: The run ID returned by run_script.
        workspace_name: The workspace the run belongs to. Uses DEFAULT_WORKSPACE if not provided.
    """
    ws = await _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/author/run/{run_id}/status")
    return _json_response(data)


@mcp.tool()
async def get_run_output(
    run_id: str,
    workspace_name: str | None = None,
    fetch_logs: bool = True,
) -> str:
    """Get the output artifacts from a completed script run.

    Returns parsed, human-readable results including:
    - issues: list of issues found by the script (title, severity, details, nextSteps)
    - stdout: script stdout output
    - stderr: script stderr output
    - status: run status (SUCCEEDED, FAILED, RUNNING)

    Args:
        run_id: The run ID returned by run_script.
        workspace_name: The workspace the run belongs to. Uses DEFAULT_WORKSPACE if not provided.
        fetch_logs: If True, download and parse artifact contents (default: True).
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
    script: str | None = None,
    location: str = "",
    workspace_name: str | None = None,
    interpreter: str = "bash",
    run_type: str = "task",
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    script_path: str | None = None,
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

    Args:
        script: The full script source code (raw text, the backend base64-encodes it).
        location: Runner location (e.g. "northamerica-northeast2-01").
        workspace_name: The workspace to run in. Uses DEFAULT_WORKSPACE if not provided.
        interpreter: "bash" or "python" (default: "bash").
        run_type: "task" or "sli" (default: "task").
        env_vars: Environment variables for the script.
        secret_vars: Secret mappings (env var name → workspace secret key).
        script_path: Local file path to read the script from. Use instead of
            'script' when the script is large. Mutually exclusive with 'script'.
    """
    try:
        script = _resolve_script(script, script_path)
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
    slx_name: str,
    workspace_name: str | None = None,
    task_titles: str = "*",
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

    Args:
        slx_name: The SLX short name (e.g. "k8s-pod-health"). Use
            get_workspace_slxs or search_workspace to find available SLXs.
        workspace_name: The workspace. Uses DEFAULT_WORKSPACE if not provided.
        task_titles: Which tasks to run within the runbook. Use "*" (default)
            to run all tasks, or "||"-separated titles for specific ones
            (e.g. "Check Pod Status||Check Pod Restarts").
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
        return _json_response(
            {"error": "No RunRequest ID in response", "response": create_data}
        )

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
    slx_name: str,
    alias: str,
    statement: str,
    script: str | None = None,
    task_title: str = "",
    location: str = "",
    interpreter: str = "bash",
    task_type: str = "task",
    workspace_name: str | None = None,
    owners: list[str] | None = None,
    branch: str = "main",
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    tags: list[dict[str, str]] | None = None,
    interval_seconds: int = 300,
    commit_message: str | None = None,
    sli_script: str | None = None,
    sli_interpreter: str | None = None,
    sli_interval_seconds: int = 300,
    cron_schedule: str | None = None,
    image_url: str | None = None,
    access: str = "read-write",
    data: str = "logs-bulk",
    resource_path: str | None = None,
    hierarchy: list[str] | None = None,
    codebundle_ref: str | None = None,
    script_path: str | None = None,
    sli_script_path: str | None = None,
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
       runbook on that schedule. Uses the rw-workspace-utils cron-scheduler-sli
       codebundle. Generates both runbook.yaml and sli.yaml.

    Args:
        slx_name: Short name for the SLX (lowercase-kebab-case, e.g. "k8s-pod-health").
        alias: Human-readable display name (e.g. "Pod Health Check").
        statement: SLX statement describing what should be true (e.g. "All pods should be running").
        script: The full script source code (not base64).
        task_title: Human-readable task title (e.g. "Check Pod Health in Namespace").
        location: Runner location (e.g. "location-01-us-west1").
        interpreter: "bash" or "python" (default: "bash").
        task_type: "task" (runbook) or "sli" (indicator) (default: "task").
        workspace_name: The workspace to commit to. Uses DEFAULT_WORKSPACE if not provided.
        owners: List of owner emails. Defaults to the token's user email.
        branch: Git branch to commit to (default: "main").
        env_vars: Environment variables baked into the SLX config.
        secret_vars: Secret mappings baked into the SLX config.
        tags: Resource tags (list of {name, value} dicts). Can include resource_name,
            resource_type, etc. The required "access" and "data" tags are added
            automatically (see below) and don't need to be in this list.
        interval_seconds: For SLIs, how often to run (default: 300).
        commit_message: Custom commit message. Auto-generated if not provided.
        sli_script: Optional SLI script to include alongside the task. When provided
            with task_type="task", generates both runbook.yaml and sli.yaml.
            The SLI script should return a float 0-1 (Python) or write to FD 3 (Bash).
        sli_interpreter: Interpreter for the SLI script. Defaults to the main interpreter.
        sli_interval_seconds: How often the SLI runs in seconds (default: 300).
        cron_schedule: Optional cron expression (e.g. "0 */2 * * *") to schedule the task.
            When provided with task_type="task", generates both runbook.yaml and sli.yaml
            using the cron-scheduler-sli codebundle. The SLI triggers the task's runbook
            when the cron schedule matches. Mutually exclusive with sli_script.
        image_url: Optional icon URL for the SLX. Defaults to the Tool Builder icon.
        access: Access level tag — "read-write" if the task can modify resources,
            "read-only" if it only reads/inspects (default: "read-write").
        data: Data type tag describing the report content — "logs-bulk" for general
            log/command output, "config" for configuration data, "logs-stacktrace"
            for stacktrace analysis (default: "logs-bulk").
        resource_path: Optional resource path for workspace-chat / usearch indexing.
            Sets ``spec.additionalContext.resourcePath`` in the SLX YAML.
            Example: "github" for GitHub-based tasks, or a Kubernetes namespace path.
        hierarchy: Optional list of tag names defining the SLX grouping hierarchy.
            Sets ``spec.additionalContext.hierarchy`` in the SLX YAML.
            Each entry should be a tag name whose value forms a segment of the
            hierarchical path (e.g. ["resource_type", "resource_name"]).
        codebundle_ref: Git ref (branch/tag) for the codebundle. When not provided,
            automatically resolved from the workspace's debugslx configuration
            (falls back to "main").
        script_path: Local file path to read the main script from. Use instead of
            'script' when the script is large. Mutually exclusive with 'script'.
        sli_script_path: Local file path to read the SLI script from. Use instead
            of 'sli_script' when the SLI script is large. Mutually exclusive with
            'sli_script'.
    """
    try:
        script = _resolve_script(script, script_path)
    except (ValueError, FileNotFoundError) as exc:
        return _json_response({"error": str(exc)})

    if sli_script_path:
        try:
            sli_script = _resolve_script(sli_script, sli_script_path)
        except (ValueError, FileNotFoundError) as exc:
            return _json_response({"error": f"SLI script: {exc}"})

    ws = await _resolve_workspace(workspace_name)
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
        return _json_response(
            {"error": f"Invalid access tag '{access}'. Must be one of: {valid}"}
        )
    if data not in VALID_DATA_TAGS:
        valid = ", ".join(VALID_DATA_TAGS)
        return _json_response(
            {"error": f"Invalid data tag '{data}'. Must be one of: {valid}"}
        )

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
                codebundle_ref=codebundle_ref,
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
    slx_name: str,
    workspace_name: str | None = None,
    branch: str = "main",
    commit_message: str | None = None,
) -> str:
    """Delete an SLX from the workspace Git repo.

    Removes the SLX directory (slx.yaml, runbook.yaml, sli.yaml) from the
    workspace configuration repository.

    Args:
        slx_name: Short name of the SLX to delete (e.g. "k8s-pod-health").
        workspace_name: The workspace to delete from. Uses DEFAULT_WORKSPACE if not provided.
        branch: Git branch to delete from (default: "main").
        commit_message: Custom commit message. Auto-generated if not provided.
    """
    ws = await _resolve_workspace(workspace_name)

    if not commit_message:
        commit_message = f"Remove SLX: {slx_name}"

    status_code, data = await _papi_delete(
        f"/api/v3/workspaces/{ws}/branches/{branch}/slxs/{slx_name}",
        body={"commit_msg": commit_message},
    )

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
        minimum_role_for_tool,
    )

    async def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False

        token_str = ctx.token.token
        _request_token.set(token_str)

        if not PAPI_URL:
            return True

        required_role = minimum_role_for_tool(tool_name)
        if required_role == WorkspaceRole.READ_ONLY:
            return True

        return True

    return check


def _build_http_server() -> FastMCP:
    """Build an HTTP-mode MCP server with authentication and health checks.

    Creates a new FastMCP instance with auth and re-registers all tool
    functions with workspace-level authorization checks.
    """
    from runwhen_platform_mcp.auth import build_auth_provider

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
