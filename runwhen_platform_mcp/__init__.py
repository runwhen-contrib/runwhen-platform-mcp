"""RunWhen Platform MCP Server — RunWhen Platform MCP for Cursor, Claude Desktop, etc."""

try:
    from importlib.metadata import version as _v
    __version__ = _v("runwhen-platform-mcp")
except (ImportError, ModuleNotFoundError):
    __version__ = "0.0.0"
