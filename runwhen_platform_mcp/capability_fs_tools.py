"""The capability build tools: browsing and editing a workspace's capability tree.

``cap_ls``, ``cap_read``, ``cap_glob``, ``cap_grep``, ``cap_write``, ``cap_edit``,
``cap_test``, ``cap_diff`` and ``cap_submit`` give an agent filesystem-shaped access
to a workspace's custom capabilities -- a tree rooted at ``/capabilities`` holding an
authoring guide, a manifest schema, and one folder per capability (custom or
packaged). Together they cover browsing, drafting, testing and proposing a capability
for a person to publish.

Each tool's name, description, HTTP method, route and JSON Schema parameters are
vendored verbatim, at import time, from ``capability_fs_tools.json`` -- the same
manifest the platform API serves at its own tools-introspection route -- so the nine
tool definitions in ``server.py`` can never drift from what the route actually takes.
This module only loads and indexes that manifest; ``server.py`` makes the HTTP calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_MANIFEST_PATH = Path(__file__).resolve().parent / "capability_fs_tools.json"

#: The vendored capability-fs tool manifest, parsed as-is.
CAPABILITY_FS_MANIFEST: dict[str, Any] = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

#: Manifest tool entries keyed by name, in the manifest's own order.
CAPABILITY_FS_TOOLS: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in CAPABILITY_FS_MANIFEST["tools"]
}

#: Tools that only read the capability tree; every other tool writes, runs or
#: publishes it.
CAPABILITY_FS_READ_ONLY_TOOLS = frozenset(
    {"cap_ls", "cap_read", "cap_glob", "cap_grep", "cap_diff"}
)


def tool_description(name: str) -> str:
    """The manifest's description for tool ``name``, verbatim."""
    return CAPABILITY_FS_TOOLS[name]["description"]


def param_description(name: str, param: str) -> str:
    """The manifest's description for one of tool ``name``'s parameters, verbatim."""
    return CAPABILITY_FS_TOOLS[name]["parameters"]["properties"][param]["description"]
