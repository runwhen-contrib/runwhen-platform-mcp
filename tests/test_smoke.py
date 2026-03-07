"""Smoke tests: package and entry point are loadable."""


def test_package_imports() -> None:
    """Server module and main entry point can be imported."""
    from runwhen_platform_mcp import __version__
    from runwhen_platform_mcp.server import main

    assert __version__ in ("0.0.0",) or __version__.replace(".", "").isdigit()
    assert callable(main)
