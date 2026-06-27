"""Skills data-package for the RunWhen Platform MCP server.

This file marks the ``skills/`` tree as a setuptools-discoverable package so
``SKILL.md`` files (and their ``references/`` subdirectories) are bundled
into the wheel and shipped in the published Docker image.

There is no Python code to expose here — skills are markdown documents
addressed at runtime via :func:`runwhen_platform_mcp.server._skills_root`,
which prefers :mod:`importlib.resources` lookup of this package so both
editable and non-editable installs find the same content.
"""
