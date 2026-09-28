"""Capability tasks over MCP: which tasks apply to a resource, and describing or running one.

``cap_list`` presents every capability task that applies to a resource the way
an MCP tool is presented -- a ``name`` (``"capability/task"``), a
``description``, an ``inputSchema`` and one-line output summaries -- so a client
can pick one and call ``cap_run`` with it. ``cap_run`` describes a task (full
input and output JSON Schemas, the latest real output, optional TypedDict / jq
renderings) or runs it against a resource.

Both tools return ``structuredContent`` that conforms to the ``outputSchema``
they declare (``CAP_LIST_OUTPUT_SCHEMA`` / ``CAP_RUN_OUTPUT_SCHEMA``). Every
output value a run returns is tagged with the ``$id`` and ``version`` of the
JSON Schema it conforms to; the full schemas ride along under ``schemas``.

Everything here is pure: the tool functions in ``server.py`` make the HTTP calls
and hand the responses to the builders below.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

#: The request a task that runs synchronously through a platform CLI takes,
#: whichever CLI serves it.
CLI_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                'One command without the leading CLI name, e.g. "get pods -n x". '
                "No shell: no pipes, redirects or ';'."
            ),
        },
        "maxBytes": {
            "type": "integer",
            "description": "Output cap in bytes; the CLI's own maximum still applies.",
        },
        "timeoutSeconds": {
            "type": "integer",
            "description": "Deadline in seconds; the CLI's own maximum still applies.",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

OPEN_STATUSES = frozenset({"queued", "running"})


class CapTask(BaseModel):
    """One capability task that applies to a resource, shaped like an MCP tool."""

    name: str = Field(description='"capability/task" -- pass it to cap_run as `task`.')
    description: str = ""
    readOnly: bool = Field(description="Only read-only tasks can be run.")
    path: str = Field(description="The capability's place in the capability file tree.")
    file: str = Field(description="The file in that tree that defines the task.")
    inputSchema: dict[str, Any] = Field(description="JSON Schema for the task's inputs.")
    outputs: dict[str, str] = Field(
        default_factory=dict, description="Output name -> one-line summary of its schema."
    )


class CapListResult(BaseModel):
    """The capability tasks that apply to one resource."""

    resource: str
    tasks: list[CapTask]
    notApplicableCount: int = 0


class CapRunResult(BaseModel):
    """A described task (operation ``describe``) or a run of one (operation ``run``)."""

    operation: Literal["describe", "run"]
    task: str
    path: str | None = None
    file: str | None = None
    # describe
    version: str | None = None
    description: str | None = None
    readOnly: bool | None = None
    invocation: list[str] | None = None
    inputSchema: dict[str, Any] | None = None
    outputSchema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema of the task's outputs, keyed by output name.",
    )
    example: dict[str, Any] | None = None
    rendering: str | None = None
    # run
    status: str | None = Field(
        default=None,
        description="queued | running | succeeded | skipped | failed | cancelled | expired | shed",
    )
    runId: str | None = None
    outputs: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description='Output name -> {"schema": {"$id", "version"}, "value": ...}.',
    )
    schemas: dict[str, dict[str, Any]] | None = Field(
        default=None, description="Full output JSON Schemas keyed by $id."
    )
    error: str | None = None
    message: str | None = None


CAP_LIST_OUTPUT_SCHEMA: dict[str, Any] = CapListResult.model_json_schema()
CAP_RUN_OUTPUT_SCHEMA: dict[str, Any] = CapRunResult.model_json_schema()


def split_task(task: str) -> tuple[str, str] | None:
    """``"custom/pg-health/pool_errors"`` -> ``("custom/pg-health", "pool_errors")``.

    A capability name may itself contain ``/``; the task is the last segment.
    """
    capability, _, name = (task or "").strip().rpartition("/")
    return (capability, name) if capability and name else None


def task_endpoint(workspace: str, capability: str, task: str) -> str:
    """The API path of one capability task."""
    return (
        f"/api/v4/workspaces/{workspace}/capabilities/"
        f"{quote(capability, safe='/')}/tasks/{quote(task, safe='')}"
    )


def _capability_path(capability: str) -> str:
    return f"/capabilities/{capability}"


def list_result(resource: str, listing: dict[str, Any]) -> CapListResult:
    """Build ``cap_list``'s result from the capabilities listing for ``resource``."""
    tasks = []
    for item in listing.get("applicable") or []:
        if not isinstance(item, dict) or not item.get("capability") or not item.get("task"):
            continue
        path = item.get("path") or _capability_path(item["capability"])
        tasks.append(
            CapTask(
                name=f"{item['capability']}/{item['task']}",
                description=item.get("description") or "",
                readOnly=bool(item.get("readOnly")),
                path=path,
                file=item.get("file") or f"{path}/capability.yaml",
                inputSchema=item.get("inputSchema") or {"type": "object"},
                outputs={
                    o["name"]: o.get("summary") or ""
                    for o in item.get("outputs") or []
                    if isinstance(o, dict) and o.get("name")
                },
            )
        )
    return CapListResult(
        resource=resource,
        tasks=tasks,
        notApplicableCount=int(listing.get("notApplicableCount") or 0),
    )


def describe_result(task: str, described: dict[str, Any]) -> CapRunResult:
    """Build ``cap_run``'s describe result from a task describe."""
    outputs = described.get("outputs") or {}
    properties = {
        name: out.get("schema") or {} for name, out in outputs.items() if isinstance(out, dict)
    }
    invocation = list(described.get("invocation") or ["queued"])
    sync = "sync" in invocation
    return CapRunResult(
        operation="describe",
        task=task,
        path=described.get("path"),
        file=described.get("file"),
        version=described.get("version"),
        description=described.get("description"),
        readOnly=described.get("readOnly"),
        invocation=invocation,
        inputSchema=CLI_INPUT_SCHEMA if sync else described.get("inputSchema"),
        outputSchema={"type": "object", "properties": properties},
        example=described.get("example"),
        rendering=described.get("rendering"),
    )


def run_result(task: str, view: dict[str, Any]) -> CapRunResult:
    """Build ``cap_run``'s run result from a run's status view."""
    status = view.get("status")
    run_id = view.get("runUuid")
    message = None
    if status in OPEN_STATUSES:
        message = (
            f"Still {status}. Call cap_run again with operation=run, task={task} and "
            f"run_id={run_id} to keep waiting; do not start another run."
        )
    return CapRunResult(
        operation="run",
        task=task,
        path=view.get("path"),
        file=view.get("file"),
        status=status,
        runId=str(run_id) if run_id else None,
        outputs=view.get("outputs") or {},
        schemas=view.get("schemas") or None,
        error=view.get("error"),
        message=message,
    )


def cli_run_result(task: str, described: dict[str, Any], response: dict[str, Any]) -> CapRunResult:
    """Build ``cap_run``'s run result from a synchronous platform CLI call.

    The CLI's response becomes the task's one declared output, in the shape
    that output's schema describes.
    """
    stdout = response.get("stdout") or ""
    value = {
        "argv": response.get("argv") or [],
        "exitCode": response.get("exitCode"),
        "stdout": stdout,
        "stderr": response.get("stderr") or "",
        "truncated": bool(response.get("truncated")),
        "stdoutBytes": len(stdout.encode("utf-8")),
        "durationMs": response.get("durationMs"),
    }
    outputs = described.get("outputs") or {}
    name, declared = next(iter(outputs.items()), ("result", {}))
    schema = (declared or {}).get("schema") or {}
    ref = {"$id": schema["$id"], "version": schema.get("version")} if schema.get("$id") else None
    return CapRunResult(
        operation="run",
        task=task,
        path=described.get("path"),
        file=described.get("file"),
        status="succeeded",
        outputs={name: {"schema": ref, "value": value}},
        schemas={schema["$id"]: schema} if ref else None,
    )


def cli_input_errors(inputs: dict[str, Any]) -> list[str]:
    """Every problem with a synchronous CLI task's ``inputs``, as ``"inputs.<name>: ..."`` lines."""
    allowed = CLI_INPUT_SCHEMA["properties"]
    errors = [
        f"inputs.{key}: not an input of this task (inputs: {', '.join(allowed)})"
        for key in inputs
        if key not in allowed
    ]
    command = inputs.get("command")
    if not isinstance(command, str) or not command.strip():
        errors.append("inputs.command: required -- " + allowed["command"]["description"])
    for key in ("maxBytes", "timeoutSeconds"):
        if inputs.get(key) is not None and not isinstance(inputs[key], int):
            errors.append(f"inputs.{key}: must be an integer")
    return errors
