"""Resolve bundled skill/reference paths for wheel, editable, and airgap installs."""

from __future__ import annotations

import os
from pathlib import Path


def skills_root() -> Path:
    """Return the canonical ``skills/`` directory.

    Matches ``server._skills_root()`` resolution order:
    ``RUNWHEN_SKILLS_DIR`` → importlib.resources ``skills`` package → repo layout.
    """
    override = os.environ.get("RUNWHEN_SKILLS_DIR")
    if override:
        return Path(override).expanduser().resolve()

    try:
        from importlib.resources import files as resource_files

        bundled = resource_files("skills")
        bundled_path = Path(str(bundled)).resolve()
        if bundled_path.is_dir():
            return bundled_path
    except (ModuleNotFoundError, AttributeError, TypeError, OSError):
        pass

    return (Path(__file__).resolve().parent.parent / "skills").resolve()


def authoring_references_root() -> Path:
    """Bundled ``author-generation-rules/references`` tree (airgap-safe)."""
    return skills_root() / "author-generation-rules" / "references"


def is_airgap_mode() -> bool:
    """True when ``RUNWHEN_AIRGAP`` is set (no outbound docs/registry assumed)."""
    value = os.environ.get("RUNWHEN_AIRGAP", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}
