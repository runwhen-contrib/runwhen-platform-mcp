"""End-to-end Tool Builder flow validation.

Exercises the full agent authoring sequence with mocked PAPI:

  validate_script → run_script_and_wait → commit_slx → run_slx

The run_slx step uses camelCase RunSession payloads (``runRequests`` /
``responseTime``) to guard the polling regression that caused 300s timeouts
when only snake_case keys were checked.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest import mock

from runwhen_platform_mcp.server import (
    commit_slx,
    run_script_and_wait,
    run_slx,
    validate_script,
)

WORKSPACE = "test-ws"
SLX_NAME = "e2e-pod-restart-check"
TASK_TITLE = "Pod Restart Check"
TASK_SCRIPT = '''"""Report pods with container restarts (e2e fixture)."""
import os


def main():
    namespace = os.environ.get("NAMESPACE", "runwhen-env-test")
    threshold = int(os.environ.get("RESTART_THRESHOLD", "1"))
    return [
        {
            "issue title": f"1 pod(s) with restarts in {namespace}",
            "issue description": (
                f"Found 1 pod(s) with container restartCount >= {threshold} "
                f"in `{namespace}` (of 52 total pods):\\n"
                "- `rw-usearch-indexer-abc` phase=Running restarts=usearch-indexer:4"
            ),
            "issue severity": 2,
            "issue next steps": (
                f"Inspect events and logs in namespace `{namespace}`."
            ),
        }
    ]
'''

MOCK_ISSUES = [
    {
        "issue title": "1 pod(s) with restarts in runwhen-env-test",
        "issue description": (
            "Found 1 pod(s) with container restartCount >= 1 "
            "in `runwhen-env-test` (of 52 total pods):\n"
            "- `rw-usearch-indexer-abc` phase=Running restarts=usearch-indexer:4"
        ),
        "issue severity": 2,
        "issue next steps": "Inspect events and logs in namespace `runwhen-env-test`.",
    }
]

RESOLVED_KUBECONFIG = "k8s:file@secret/kubeconfig:kubeconfig"
SECRET_NOTES = [
    "Resolved secret_vars['kubeconfig'] to workspaceKey "
    f"'{RESOLVED_KUBECONFIG}' (copied pattern from detect-dev-activity)."
]

COMMIT_KWARGS = {
    "slx_name": SLX_NAME,
    "alias": "Pod Restart Check",
    "statement": "Pods should not have excessive container restarts",
    "workspace_name": WORKSPACE,
    "script": TASK_SCRIPT,
    "task_title": TASK_TITLE,
    "interpreter": "python",
    "task_type": "task",
    "access": "read-only",
    "data": "config",
    "resource_path": "custom/kubernetes/gke/shared-cluster/runwhen-env-test/pods",
    "hierarchy": ["platform", "kubernetes", "gke", "shared-cluster", "runwhen-env-test"],
    "env_vars": {
        "KUBE_CONTEXT": "gke_example_us-west1_cluster",
        "NAMESPACE": "runwhen-env-test",
        "RESTART_THRESHOLD": "1",
    },
    "secret_vars": {"kubeconfig": "kubeconfig"},
    "tags": [
        {"name": "access", "value": "read-only"},
        {"name": "data", "value": "config"},
        {"name": "platform", "value": "custom"},
    ],
}


def _run(coro):
    return asyncio.run(coro)


def _run_tool_builder_flow(session_polls: list[dict]) -> tuple[dict, dict, dict, dict, dict]:
    """Execute validate → run_script_and_wait → commit_slx → run_slx; return step JSON + mocks."""
    with _full_flow_mocks(session_polls=session_polls) as mocks:
        validate_data = json.loads(
            _run(validate_script(script=TASK_SCRIPT, interpreter="python", task_type="task"))
        )
        run_data = json.loads(
            _run(
                run_script_and_wait(
                    workspace_name=WORKSPACE,
                    script=TASK_SCRIPT,
                    interpreter="python",
                    env_vars=COMMIT_KWARGS["env_vars"],
                    secret_vars=COMMIT_KWARGS["secret_vars"],
                )
            )
        )
        commit_data = json.loads(_run(commit_slx(**COMMIT_KWARGS)))
        slx_data = json.loads(
            _run(
                run_slx(
                    slx_name=SLX_NAME,
                    workspace_name=WORKSPACE,
                    task_titles="*",
                )
            )
        )
        return validate_data, run_data, commit_data, slx_data, mocks


@contextmanager
def _full_flow_mocks(*, session_polls: list[dict]):
    """Patch PAPI and helpers for validate → test → commit → run_slx."""
    with (
        mock.patch(
            "runwhen_platform_mcp.server.asyncio.sleep",
            new=mock.AsyncMock(),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_get",
            new_callable=mock.AsyncMock,
        ) as mock_get,
        mock.patch(
            "runwhen_platform_mcp.server._papi_post",
            new_callable=mock.AsyncMock,
        ) as mock_post,
        mock.patch(
            "runwhen_platform_mcp.server._fetch_and_parse_artifacts",
            new_callable=mock.AsyncMock,
        ) as mock_parse,
        mock.patch(
            "runwhen_platform_mcp.server._resolve_workspace",
            new_callable=mock.AsyncMock,
        ) as mock_ws,
        mock.patch(
            "runwhen_platform_mcp.server._resolve_location",
            new_callable=mock.AsyncMock,
        ) as mock_location,
        mock.patch(
            "runwhen_platform_mcp.server._resolve_secret_vars_for_author",
            new_callable=mock.AsyncMock,
        ) as mock_resolve_secrets,
        mock.patch(
            "runwhen_platform_mcp.server._get_user_email",
            new_callable=mock.AsyncMock,
        ) as mock_email,
        mock.patch(
            "runwhen_platform_mcp.server._get_codebundle_ref",
            new_callable=mock.AsyncMock,
        ) as mock_ref,
        mock.patch(
            "runwhen_platform_mcp.server._sync_slx_resources",
            new_callable=mock.AsyncMock,
        ) as mock_sync,
    ):
        mock_ws.return_value = WORKSPACE
        mock_location.return_value = "stg-test--test"
        mock_resolve_secrets.return_value = (
            {"kubeconfig": RESOLVED_KUBECONFIG},
            SECRET_NOTES,
        )
        mock_email.return_value = "dev@example.com"
        mock_ref.return_value = "main"
        mock_parse.return_value = {
            "issues": MOCK_ISSUES,
            "stdout": "SUMMARY: scanned 52 pods, 1 with restarts\n",
            "stderr": "",
            "report": "Robot preflight completed",
            "artifact_urls": {
                "log": "https://example.com/report.jsonl",
                "issues": "https://example.com/issues.jsonl",
            },
        }
        mock_sync.return_value = (
            201,
            {
                "slx": {
                    "status": "created",
                    "name": f"{WORKSPACE}--{SLX_NAME}",
                    "resource_id": 1902,
                },
                "runbook": {"status": "created", "name": "1902", "resource_id": 1900},
            },
        )
        mock_get.side_effect = [
            {"secrets": []},
            {"status": "SUCCEEDED"},
            {"artifacts": []},
            {"secrets": []},
            *session_polls,
        ]
        mock_post.side_effect = [
            (200, {"runId": "run-e2e-001"}),
            (200, {"id": 9010}),
        ]
        yield {
            "get": mock_get,
            "post": mock_post,
            "sync": mock_sync,
        }


def test_tool_builder_validate_test_commit_run_slx_flow() -> None:
    """Full mocked chain: validate → author run → commit → run committed SLX."""
    session_polls = [
        {"runRequests": [{"id": 32399, "status": "running"}]},
        {
            "runRequests": [
                {
                    "id": 32399,
                    "responseTime": "2026-06-30T01:40:45.666211Z",
                    "passedTitles": TASK_TITLE,
                    "failedTitles": "",
                    "skippedTitles": "",
                    "issues": [
                        {
                            "title": MOCK_ISSUES[0]["issue title"],
                            "severity": 2,
                            "taskTitle": TASK_TITLE,
                        }
                    ],
                }
            ]
        },
    ]

    validate_data, run_data, commit_data, slx_data, mocks = _run_tool_builder_flow(session_polls)

    assert validate_data["valid"] is True, validate_data
    assert validate_data["blocking_warnings"] == []
    assert "NAMESPACE" in validate_data["detected_env_vars"]

    assert run_data["finalStatus"] == "SUCCEEDED", run_data
    assert run_data["runId"] == "run-e2e-001"
    assert len(run_data["issues"]) == 1
    assert run_data["issues"][0]["issue title"] == MOCK_ISSUES[0]["issue title"]
    assert "SUMMARY: scanned 52 pods" in run_data["stdout"]
    assert run_data["report"] == "Robot preflight completed"
    assert run_data.get("artifact_urls", {}).get("log")
    assert run_data.get("secret_resolution_notes") == SECRET_NOTES
    assert "status_interpretation" in run_data
    assert run_data["status_interpretation"]["meaning"]

    author_body = mocks["post"].call_args_list[0][0][1]
    assert author_body["secretVars"]["kubeconfig"] == RESOLVED_KUBECONFIG
    assert author_body["envVars"]["NAMESPACE"] == "runwhen-env-test"

    assert commit_data["status"] == "committed", commit_data
    assert commit_data["slx_name"] == SLX_NAME
    assert commit_data["committed_types"] == "task"
    assert commit_data.get("secret_resolution_notes") == SECRET_NOTES

    sync_kwargs = mocks["sync"].call_args.kwargs
    assert sync_kwargs["ws"] == WORKSPACE
    assert sync_kwargs["slx_name"] == SLX_NAME
    runbook = sync_kwargs["runbook_payload"]
    assert runbook is not None
    config_names = {c["name"] for c in runbook["config_provided"]}
    assert "TASK_TITLE" in config_names
    assert "NAMESPACE" in config_names
    secret_keys = {s["name"] for s in runbook["secrets_provided"]}
    assert secret_keys == {"kubeconfig"}

    assert slx_data["status"] == "completed", slx_data
    assert slx_data["session_id"] == 9010
    assert slx_data["passed_titles"] == TASK_TITLE
    assert slx_data["issues"][0]["title"] == MOCK_ISSUES[0]["issue title"]
    assert slx_data["elapsed_seconds"] >= 0

    runsession_body = mocks["post"].call_args_list[1][0][1]
    assert runsession_body["runRequests"][0]["slxName"] == f"{WORKSPACE}--{SLX_NAME}"
    assert runsession_body["runRequests"][0]["taskTitles"] == ["*"]


def test_run_slx_camelcase_polling_completes_without_snake_case_keys() -> None:
    """Regression: PAPI v3 RunSession GET uses camelCase only."""
    incomplete = {"runRequests": [{"id": 1, "status": "running"}]}
    complete = {
        "runRequests": [
            {
                "id": 1,
                "responseTime": "2026-06-30T01:00:00Z",
                "passedTitles": TASK_TITLE,
                "issues": [],
            }
        ]
    }

    with (
        mock.patch(
            "runwhen_platform_mcp.server.asyncio.sleep",
            new=mock.AsyncMock(),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_get",
            new_callable=mock.AsyncMock,
            side_effect=[incomplete, complete],
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_post",
            new_callable=mock.AsyncMock,
            return_value=(200, {"id": 9011}),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._resolve_workspace",
            new_callable=mock.AsyncMock,
            return_value=WORKSPACE,
        ),
    ):
        result = json.loads(
            _run(
                run_slx(
                    slx_name=SLX_NAME,
                    workspace_name=WORKSPACE,
                    task_titles="*",
                )
            )
        )
    assert result["status"] == "completed"
    assert result["passed_titles"] == TASK_TITLE
    assert "response_time" not in json.dumps(complete)


def test_run_slx_still_accepts_snake_case_run_requests() -> None:
    """Backward compat: snake_case RunSession payloads still complete."""
    snake_complete = {
        "run_requests": [
            {
                "id": 1,
                "response_time": "2026-06-30T01:00:00Z",
                "passed_titles": TASK_TITLE,
                "issues": [],
            }
        ]
    }

    with (
        mock.patch(
            "runwhen_platform_mcp.server.asyncio.sleep",
            new=mock.AsyncMock(),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_get",
            new_callable=mock.AsyncMock,
            return_value=snake_complete,
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_post",
            new_callable=mock.AsyncMock,
            return_value=(200, {"id": 9012}),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._resolve_workspace",
            new_callable=mock.AsyncMock,
            return_value=WORKSPACE,
        ),
    ):
        result = json.loads(
            _run(
                run_slx(
                    slx_name=SLX_NAME,
                    workspace_name=WORKSPACE,
                    task_titles="*",
                )
            )
        )
    assert result["status"] == "completed"
    assert result["passed_titles"] == TASK_TITLE


def test_run_slx_times_out_when_response_time_never_set() -> None:
    """Incomplete run requests must not spin until the client gives up."""
    incomplete = {"runRequests": [{"id": 1, "status": "running"}]}

    with (
        mock.patch(
            "runwhen_platform_mcp.server.asyncio.sleep",
            new=mock.AsyncMock(),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_get",
            new_callable=mock.AsyncMock,
            return_value=incomplete,
        ),
        mock.patch(
            "runwhen_platform_mcp.server._papi_post",
            new_callable=mock.AsyncMock,
            return_value=(200, {"id": 9013}),
        ),
        mock.patch(
            "runwhen_platform_mcp.server._resolve_workspace",
            new_callable=mock.AsyncMock,
            return_value=WORKSPACE,
        ),
        mock.patch("runwhen_platform_mcp.server.SLX_RUN_MAX_POLL_S", 0),
    ):
        result = json.loads(
            _run(
                run_slx(
                    slx_name=SLX_NAME,
                    workspace_name=WORKSPACE,
                    task_titles="*",
                )
            )
        )
    assert result["status"] == "timeout"
    assert result["session_id"] == 9013
