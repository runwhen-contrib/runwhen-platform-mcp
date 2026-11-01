"""Capability artifacts over MCP: read a large run output one bounded page at a time.

A capability run returns an output too big to inline as an artifact handle: a
dict whose ``$artifact`` is an ``art_<32 hex>`` ref, with its size and a short
``head`` / ``tail`` preview. ``cap_run`` passes a handle through as-is and
shortens any other output too big for a client's context, naming a
``run:<runUuid>/outputs/<name>`` ref for the whole of it. ``artifact_read``
reads either kind of ref back one page at a time (``head`` / ``tail`` by
lines, ``range`` by bytes), lists recent artifacts, and stats one. It never
returns a whole artifact: the platform caps a page at 2000 lines or 64 KiB,
and the request is clamped to match before it is sent.

Searching or querying an artifact is a ``cap_run`` of an ``artifact-tools/*``
task with the ``art_`` ref as an input, not a loop of page reads.

This mirrors AgentFarm's ``artifact_read`` so the same refs and hints work in
both. Everything here is pure: the tool function in ``server.py`` makes the
HTTP call and hands the response to :func:`read_result`.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeGuard
from urllib.parse import quote

#: An artifact ref, as an output's ``$artifact``.
ARTIFACT_REF = re.compile(r"art_[0-9a-f]{32}")
_RUN_UUID = r"[0-9a-f-]{36}"
#: A finished run's output. "." and ".." are refused: a URL normalizer would
#: resolve them out of the route.
RUN_OUTPUT_REF = re.compile(rf"run:({_RUN_UUID})/outputs/(?!\.{{1,2}}\Z)(.+)")
_RUN_ID = re.compile(_RUN_UUID)

OPS = ("list", "stat", "head", "tail", "range")
#: The platform's ceilings on one page.
MAX_LINES = 2000
MAX_BYTES = 65536

#: Largest JSON (in characters) one output may take in a ``cap_run`` result before it is shortened.
MAX_OUTPUT_CHARS = 24_000
#: (list items, string characters) kept per shortening pass, loosest first.
_SHRINK_STEPS = ((200, 4000), (50, 1000), (10, 300), (3, 100))

#: Each listed artifact's previews: 2 KB apiece, so a page of 20 would cost ~80 KB.
#: ``stat`` returns them for the one artifact worth a look.
_PREVIEW_KEYS = ("head", "tail")
_LIST_HINT = (
    "artifact_read(op='stat'|'head'|'tail'|'range', ref=<an item's $artifact>) to read one; "
    "stat shows its head and tail"
)


def is_artifact(value: Any) -> TypeGuard[dict[str, Any]]:
    """An artifact handle: a dict whose ``$artifact`` is a real ``art_`` ref."""
    if not isinstance(value, dict):
        return False
    ref = value.get("$artifact")
    return isinstance(ref, str) and ARTIFACT_REF.fullmatch(ref) is not None


def has_artifact_input(inputs: dict[str, Any]) -> bool:
    """Whether a top-level input is an ``art_`` ref: the platform then picks the run's target."""
    return any(isinstance(v, str) and ARTIFACT_REF.fullmatch(v) for v in inputs.values())


def artifact_next_hint(ref: str) -> str:
    """How to page, search or query the artifact ``ref``."""
    return (
        f"artifact_read(op='head'|'tail'|'range', ref='{ref}') to page; "
        "cap_run(operation='run', task='artifact-tools/grep'|'jq'|'sql'|'slice', "
        f"inputs={{'source': '{ref}', …}}) with no resource to search or query"
    )


def _shrink(value: Any, items: int, chars: int) -> Any:
    """``value`` with strings cut to ``chars`` (head and tail kept) and lists to ``items``."""
    if isinstance(value, dict):
        return {k: _shrink(v, items, chars) for k, v in value.items()}
    if isinstance(value, list):
        kept = [_shrink(v, items, chars) for v in value[:items]]
        if len(value) > items:
            kept.append({"omittedItems": len(value) - items})
        return kept
    if isinstance(value, str) and len(value) > chars:
        marker = f"\n…[{len(value) - chars} chars omitted]…\n"
        # Only cut when the result is shorter than the string it replaces.
        if len(value) > chars + len(marker):
            head = chars // 2
            return value[:head] + marker + value[-(chars - head) :]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str))


def fit_output(value: Any) -> tuple[Any, bool]:
    """``(value, shortened)``: ``value`` cut down until its JSON fits ``MAX_OUTPUT_CHARS``.

    ``None`` when no pass makes it fit.
    """
    if _size(value) <= MAX_OUTPUT_CHARS:
        return value, False
    for items, chars in _SHRINK_STEPS:
        shrunk = _shrink(value, items, chars)
        if _size(shrunk) <= MAX_OUTPUT_CHARS:
            return shrunk, True
    return None, True


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def read_request(
    workspace: str,
    op: str,
    ref: str,
    *,
    lines: int,
    offset: int,
    nbytes: int,
    task: str,
    run_id: str,
) -> tuple[str, dict[str, Any]]:
    """The API path and query params of one ``artifact_read``.

    Raises ``ValueError`` naming the problem when ``op``, ``ref`` or
    ``run_id`` cannot be read, before anything is sent. Only the params the op
    uses are sent: the platform refuses the others.
    """
    if op not in OPS:
        raise ValueError(f"op must be one of {', '.join(OPS)}; got {op!r}.")
    base = f"/api/v4/workspaces/{workspace}"
    if op == "list":
        params: dict[str, Any] = {}
        bare_task = (task or "").strip().rsplit("/", 1)[-1]
        if bare_task:
            # Artifacts carry the bare task name ("logs"), not "capability/task".
            params["task"] = bare_task
        if run_id:
            if not _RUN_ID.fullmatch(run_id.strip()):
                raise ValueError(
                    "run_id must be a run's UUID (a cap_run result's runId or an "
                    f"artifact's runUuid); got {run_id!r}."
                )
            params["runUuid"] = run_id.strip()
        return f"{base}/artifacts", params

    ref = (ref or "").strip()
    run_output = RUN_OUTPUT_REF.fullmatch(ref)
    if not ref:
        raise ValueError(f"ref is required for op={op!r}.")
    if not ARTIFACT_REF.fullmatch(ref) and not run_output:
        raise ValueError(
            "ref must be an art_ ref (an output's $artifact) or 'run:<runUuid>/outputs/<name>' "
            f"(a shortened cap_run output's more.ref); got {ref!r}."
        )
    if op == "stat":
        if run_output:
            raise ValueError(
                "stat takes an art_ ref; page a run output with op='head', 'tail' or 'range'."
            )
        return f"{base}/artifacts/{ref}", {}

    if op == "range":
        params = {"offset": max(int(offset), 0)}
        if nbytes > 0:
            params["bytes"] = _clamp(nbytes, 1, MAX_BYTES)
    else:
        params = {"lines": _clamp(lines, 1, MAX_LINES)}
    params["op"] = op
    if run_output:
        run_uuid, name = run_output.groups()
        return (
            f"{base}/capability-runs/{run_uuid}/outputs/{quote(name, safe='')}/content",
            params,
        )
    return f"{base}/artifacts/{ref}/content", params


def _hint(op: str, ref: str, view: dict[str, Any]) -> str:
    """What to do next with ``ref``: the pages around this one, then how to page or query it."""
    if op == "list":
        return _LIST_HINT
    if ARTIFACT_REF.fullmatch(ref):
        hint = artifact_next_hint(ref)
    else:
        # The artifact-tools take an art_ input, so a run output only pages.
        hint = f"artifact_read(op='head'|'tail'|'range', ref='{ref}') to page"
    if op == "stat":
        return hint
    for label, key in (("previous", "prevOffset"), ("next", "nextOffset")):
        if view.get(key) is not None:
            hint = (
                f"{label} page: artifact_read(op='range', ref='{ref}', offset={view[key]}); {hint}"
            )
    return hint


def read_result(op: str, ref: str, view: Any) -> dict[str, Any]:
    """``artifact_read``'s result: the platform's view plus ``next``, saying what to read next.

    A list drops each item's ``head`` / ``tail`` preview; everything else is
    passed through as the platform sent it (``text``, ``nextOffset``,
    ``prevOffset``, ``totalBytes``, ``encoding``, ...).
    """
    data = dict(view) if isinstance(view, dict) else {"value": view}
    if op == "list" and isinstance(data.get("items"), list):
        data["items"] = [
            {k: v for k, v in item.items() if k not in _PREVIEW_KEYS}
            if isinstance(item, dict)
            else item
            for item in data["items"]
        ]
    data["next"] = _hint(op, (ref or "").strip(), data)
    return data
