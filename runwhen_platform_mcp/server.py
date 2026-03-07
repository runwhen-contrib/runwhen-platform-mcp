"""RunWhen Platform MCP Server.

Exposes RunWhen workspace capabilities to MCP clients (Cursor, Claude Desktop, etc.)
by proxying to the RunWhen API and Agent services.

The key tool is `workspace_chat` which passes through to the RunWhen Agent's
chat endpoint, giving MCP clients access to ~25+ internal tools (issue search,
task search, resource search, knowledge base, graphing, etc.) without needing
to re-implement any of them.

The Tool Builder tools (`run_script`, `get_run_status`, `get_run_output`,
`commit_slx`) replicate the platform's "Create Task" / Tool Builder flow,
allowing agents to write scripts locally, test them against live infrastructure,
and commit them as SLXs to a workspace.

Auth flow:
  1. User provides a RunWhen API token (from POST /api/v3/token/, or a
     Personal Access Token created in the UI)
  2. That same token is used for both API and Agent requests
  3. The Agent service validates the token by calling back to the API
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

PAPI_URL = os.environ.get("RW_API_URL", "").rstrip("/")
RUNWHEN_TOKEN = os.environ.get("RUNWHEN_TOKEN", "")
DEFAULT_WORKSPACE = os.environ.get("DEFAULT_WORKSPACE", "")


def _derive_agentfarm_url(api_url: str) -> str:
    """Derive the AgentFarm URL by swapping the ``papi`` subdomain for ``agentfarm``."""
    return re.sub(r"://papi\.", "://agentfarm.", api_url) if api_url else ""


AGENTFARM_URL = _derive_agentfarm_url(PAPI_URL)
RUNWHEN_CONTEXT_FILE = os.environ.get("RUNWHEN_CONTEXT_FILE", "")

mcp = FastMCP(
    "RunWhen Platform",
    instructions=(
        "RunWhen Platform MCP server. Use `workspace_chat` to ask the RunWhen AI assistant "
        "about your infrastructure — it can search issues, tasks, run sessions, resources, "
        "knowledge base articles, and more. Use the other tools for direct data access.\n\n"
        "Tool Builder: Use `run_script` to test bash/python scripts against live infrastructure, "
        "`get_run_status` and `get_run_output` to monitor and retrieve results, and `commit_slx` "
        "to save a tested script as an SLX in the workspace. `commit_slx` supports creating "
        "both a task (runbook) and SLI together — either a custom SLI script or a cron-scheduled "
        "SLI that triggers the runbook on a schedule. Use `get_workspace_secrets` to "
        "discover available secrets.\n\n"
        "IMPORTANT — Before writing any task or script, call `get_workspace_context` to load "
        "domain-specific rules from the project's RUNWHEN.md file. This includes infrastructure "
        "conventions, database access rules, naming patterns, and other constraints that scripts "
        "must follow.\n\n"
        "IMPORTANT — Issue format for task scripts:\n"
        "  Python: return a list of dicts with keys: 'issue title', 'issue description', "
        "'issue severity' (int 1-4), 'issue next steps', and optionally 'issue observed at'.\n"
        "  Bash: write JSON array to FD 3 with keys: 'issue title', 'issue description', "
        "'issue severity', 'issue next steps'.\n\n"
        "IMPORTANT — Required SLX tags (set via commit_slx parameters):\n"
        "  access: 'read-write' (task modifies resources) or 'read-only' (task only inspects).\n"
        "  data: 'logs-bulk' (general command output), 'config' (configuration data), "
        "or 'logs-stacktrace' (stacktrace analysis).\n"
        "See docs/tool-builder-flow.md for the full contract."
    ),
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
                    result["issues"].append({
                        k: v for k, v in issue.items()
                        if k in (
                            "title", "severity", "details", "nextSteps",
                            "expected", "actual", "reproduceHint",
                            "taskName", "observedAt",
                        )
                    })
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
                            result["stdout"].append(obj[len("Command stdout: "):])
                        elif obj.startswith("Command stderr: "):
                            result["stderr"].append(obj[len("Command stderr: "):])
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
    """Get the current token, raising a clear error if missing."""
    if not RUNWHEN_TOKEN:
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
    for path in ("/api/v3/users/whoami", "/api/v3/users/whoami/"):
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
            data = await _papi_get(f"/api/v3/users/{user_id}/")
            email = data.get("primaryEmail") or data.get("primary_email")
            if email:
                _user_email_cache[token] = email
                return email
        except Exception:
            pass

    fallback = str(user_id) if user_id else "cursor@runwhen.com"
    _user_email_cache[token] = fallback
    return fallback



def _resolve_workspace(workspace_name: str | None) -> str:
    ws = workspace_name or DEFAULT_WORKSPACE
    if not ws:
        raise ValueError(
            "workspace_name is required (or set DEFAULT_WORKSPACE in .env)"
        )
    return ws


async def _papi_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Make an authenticated GET request to PAPI."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            params=params,
        )
        _raise_for_papi_status(resp, path)
        return resp.json()


async def _papi_post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """Make an authenticated POST request to PAPI. Returns (status_code, json)."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(
            f"{PAPI_URL}{path}",
            headers=_headers(),
            json=body,
        )
        _raise_for_papi_status(resp, path)
        return resp.status_code, resp.json()


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
        if task_type == "sli" and "return" in script:
            if not re.search(r"return\s+[\d.]", script):
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
        "HOME", "USER", "PATH", "SHELL", "PWD", "OLDPWD", "TERM", "LANG",
        "LC_ALL", "HOSTNAME", "RANDOM", "LINENO", "SECONDS", "PIPESTATUS",
        "BASH_SOURCE", "FUNCNAME", "IFS", "PS1", "PS2", "_",
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
        for m in re.finditer(r'\$\{?(\w+)\}?', script):
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

    User-supplied tags are preserved; ``access`` and ``data`` entries are
    added or overwritten so they always reflect the caller's intent.
    """
    merged: dict[str, str] = {}
    for tag in tags or []:
        merged[tag["name"]] = tag["value"]
    merged["access"] = access
    merged["data"] = data
    return [{"name": k, "value": v} for k, v in merged.items()]


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
) -> str:
    """Generate slx.yaml content."""
    spec: dict[str, Any] = {
        "alias": alias,
        "imageURL": image_url or GENERIC_SLX_ICON,
        "statement": statement,
        "owners": owners,
        "tags": _ensure_required_tags(tags, access, data),
    }

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


def _build_runbook_yaml(
    workspace: str,
    slx_name: str,
    script_b64: str,
    interpreter: str,
    task_title: str,
    location: str,
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
) -> str:
    """Generate runbook.yaml content for a Tool Builder task."""
    config_provided = [
        {"name": "TASK_TITLE", "value": task_title},
        {"name": "GEN_CMD", "value": script_b64},
        {"name": "INTERPRETER", "value": interpreter},
    ]

    env_vars = env_vars or {}
    secret_vars = secret_vars or {}

    config_provided.append(
        {"name": "CONFIG_ENV_MAP", "value": json.dumps(env_vars)}
    )
    config_provided.append(
        {"name": "SECRET_ENV_MAP", "value": json.dumps(list(secret_vars.keys()))}
    )

    for k, v in env_vars.items():
        config_provided.append({"name": k, "value": v})

    secrets_provided = [
        {"name": k, "workspaceKey": v} for k, v in secret_vars.items()
    ]

    spec: dict[str, Any] = {
        "location": location,
        "codeBundle": dict(RB_CODE_BUNDLE),
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
) -> str:
    """Generate sli.yaml content for a Tool Builder SLI."""
    config_provided = [
        {"name": "GEN_CMD", "value": script_b64},
        {"name": "INTERPRETER", "value": interpreter},
    ]

    env_vars = env_vars or {}
    secret_vars = secret_vars or {}

    config_provided.append(
        {"name": "CONFIG_ENV_MAP", "value": json.dumps(env_vars)}
    )
    config_provided.append(
        {"name": "SECRET_ENV_MAP", "value": json.dumps(list(secret_vars.keys()))}
    )

    for k, v in env_vars.items():
        config_provided.append({"name": k, "value": v})

    secrets_provided = [
        {"name": k, "workspaceKey": v} for k, v in secret_vars.items()
    ]

    spec: dict[str, Any] = {
        "location": location,
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
        "codeBundle": dict(SLI_CODE_BUNDLE),
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
    """
    config_provided = [
        {"name": "CRON_SCHEDULE", "value": cron_schedule},
        {"name": "DRY_RUN", "value": "true" if dry_run else "false"},
    ]
    if target_slx:
        config_provided.append({"name": "TARGET_SLX", "value": target_slx})

    spec: dict[str, Any] = {
        "location": location,
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
        "codeBundle": dict(CRON_SLI_CODE_BUNDLE),
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
                        f"AgentFarm returned 401 Unauthorized. "
                        "Your RUNWHEN_TOKEN may be expired or invalid."
                    )
                if resp.status_code == 403:
                    raise ValueError(
                        f"AgentFarm returned 403 Forbidden. "
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
            raise ValueError(
                f"AgentFarm {method} {path}: {resp.status_code} {resp.text[:500]}"
            )
        if resp.status_code == 204:
            return {}
        return resp.json()


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
    ws = _resolve_workspace(workspace_name)
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
    return json.dumps(result, indent=2)


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces you have access to.

    Returns workspace names, display names, and basic metadata.
    """
    data = await _papi_get("/api/v3/workspaces/")
    workspaces = data if isinstance(data, list) else data.get("results", data)
    summary = []
    for ws in workspaces:
        name = ws.get("name") or ws.get("shortName") or ws.get("short_name", "")
        display = ws.get("displayName") or ws.get("display_name") or name
        summary.append({"name": name, "displayName": display})
    return json.dumps(summary, indent=2)


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
    ws = _resolve_workspace(workspace_name)
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
        return json.dumps({"error": str(e)}, indent=2)
    return json.dumps(data, indent=2)


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
        return json.dumps(data, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def list_chat_rules(
    scope_type: str | None = None,
    scope_id: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> str:
    """List chat rules (workspace chat rules). Uses AgentFarm internal API; may require network access.

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
    ws = _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"limit": limit}
    if severity is not None:
        params["severity"] = severity
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues/", params=params)
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs/")
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    params: dict[str, Any] = {"limit": limit}
    data = await _papi_get(f"/api/v3/workspaces/{ws}/runsessions/", params=params)
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(
        f"/api/v3/workspaces/{ws}/workspace-configuration-index"
    )
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/issues/{issue_id}/")
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/slxs/{slx_name}/runbook/")
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(
        f"/api/v3/workspaces/{ws}/autocomplete/",
        params={"q": query},
    )
    return json.dumps(data, indent=2)


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
        return json.dumps({
            "status": "no_context",
            "message": (
                "No RUNWHEN.md file found. Create a RUNWHEN.md in your project root "
                "describing infrastructure conventions, database access rules, naming "
                "patterns, and other constraints. See the MCP server docs for the "
                "recommended format."
            ),
        })

    return json.dumps({
        "status": "ok",
        "source": ctx["path"],
        "content": ctx["content"],
    })


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/secrets-keys")
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(f"/api/v3/workspaces/{ws}/locations/")
    return json.dumps(data, indent=2)


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

    return json.dumps(result, indent=2)


@mcp.tool()
async def run_script(
    script: str,
    location: str,
    workspace_name: str | None = None,
    interpreter: str = "bash",
    run_type: str = "task",
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
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
        location: Runner location (e.g. "northamerica-northeast2-01"). Use get_workspace_locations to list.
        workspace_name: The workspace to run in. Uses DEFAULT_WORKSPACE if not provided.
        interpreter: "bash" or "python" (default: "bash").
        run_type: "task" or "sli" (default: "task").
        env_vars: Environment variables for the script (e.g. {"NAMESPACE": "default"}).
        secret_vars: Secret mappings (env var name → workspace secret key, e.g. {"kubeconfig": "kubeconfig"}).
    """
    ws = _resolve_workspace(workspace_name)

    warnings = _validate_script(script, interpreter, run_type)
    if warnings:
        return json.dumps({
            "error": "Script validation failed",
            "warnings": warnings,
            "message": "Fix the warnings and try again. Use validate_script for details.",
        }, indent=2)

    body: dict[str, Any] = {
        "command": script,
        "location": location,
        "run_type": run_type,
        "interpreter": interpreter,
        "envVars": env_vars or {},
        "secretVars": secret_vars or {},
    }

    status_code, data = await _papi_post(
        f"/api/v3/workspaces/{ws}/author/run", body
    )
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(
        f"/api/v3/workspaces/{ws}/author/run/{run_id}/status"
    )
    return json.dumps(data, indent=2)


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
    ws = _resolve_workspace(workspace_name)
    data = await _papi_get(
        f"/api/v3/workspaces/{ws}/author/run/{run_id}/output"
    )

    if not fetch_logs or not isinstance(data, dict):
        return json.dumps(data, indent=2)

    parsed = await _fetch_and_parse_artifacts(data)
    result = {
        "runId": run_id,
        "status": data.get("status", "UNKNOWN"),
        "issues": parsed["issues"],
        "stdout": parsed["stdout"],
        "stderr": parsed["stderr"],
        "report": parsed["report"],
    }
    return json.dumps(result, indent=2)


@mcp.tool()
async def run_script_and_wait(
    script: str,
    location: str,
    workspace_name: str | None = None,
    interpreter: str = "bash",
    run_type: str = "task",
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
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
    """
    ws = _resolve_workspace(workspace_name)

    warnings = _validate_script(script, interpreter, run_type)
    if warnings:
        return json.dumps({
            "error": "Script validation failed",
            "warnings": warnings,
        }, indent=2)

    body: dict[str, Any] = {
        "command": script,
        "location": location,
        "run_type": run_type,
        "interpreter": interpreter,
        "envVars": env_vars or {},
        "secretVars": secret_vars or {},
    }

    _, run_data = await _papi_post(
        f"/api/v3/workspaces/{ws}/author/run", body
    )

    run_id = run_data.get("runId")
    if not run_id:
        return json.dumps({"error": "No runId in response", "response": run_data}, indent=2)

    elapsed = 0
    status = "RUNNING"
    while status == "RUNNING" and elapsed < MAX_POLL_DURATION_S:
        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S
        status_data = await _papi_get(
            f"/api/v3/workspaces/{ws}/author/run/{run_id}/status"
        )
        status = status_data.get("status", "UNKNOWN")

    await asyncio.sleep(ARTIFACT_SETTLE_DELAY_S)

    output_data = await _papi_get(
        f"/api/v3/workspaces/{ws}/author/run/{run_id}/output"
    )

    parsed: dict[str, Any] = {"issues": [], "stdout": "", "stderr": "", "report": ""}
    if isinstance(output_data, dict):
        parsed = await _fetch_and_parse_artifacts(output_data)
        if not parsed["stdout"] and not parsed["issues"] and output_data.get("artifacts"):
            await asyncio.sleep(3)
            output_data = await _papi_get(
                f"/api/v3/workspaces/{ws}/author/run/{run_id}/output"
            )
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
    return json.dumps(result, indent=2)


@mcp.tool()
async def commit_slx(
    slx_name: str,
    alias: str,
    statement: str,
    script: str,
    task_title: str,
    location: str,
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
    """
    ws = _resolve_workspace(workspace_name)
    script_b64 = base64.b64encode(script.encode()).decode()

    if sli_script and cron_schedule:
        return json.dumps({
            "error": "Cannot specify both sli_script and cron_schedule. "
                     "Use sli_script for a custom SLI metric, or cron_schedule "
                     "to trigger the runbook on a schedule.",
        }, indent=2)

    if access not in VALID_ACCESS_TAGS:
        return json.dumps({
            "error": f"Invalid access tag '{access}'. Must be one of: {', '.join(VALID_ACCESS_TAGS)}",
        }, indent=2)
    if data not in VALID_DATA_TAGS:
        return json.dumps({
            "error": f"Invalid data tag '{data}'. Must be one of: {', '.join(VALID_DATA_TAGS)}",
        }, indent=2)

    if owners is None:
        owners = [await _get_user_email()]

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

    result = {
        "status": "created" if status_code == 201 else "updated",
        "slx_name": slx_name,
        "workspace": ws,
        "branch": branch,
        "committed_files": list(files.keys()),
        "committed_types": type_label,
        "response": data,
    }
    return json.dumps(result, indent=2)


def main() -> None:
    """Entry point for the runwhen-platform-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
