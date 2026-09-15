"""Generic validator + tests for skill "packs" (``references/pack.yaml``).

A pack manifest bundles the rules, knowledge articles, and commands a
workspace-tuning skill installs (e.g. ``configure-datadog-workspace``). This
file is deliberately generic: it discovers every
``skills/*/references/pack.yaml`` under the repo's skills root (via
``_skills_root()``) and validates each one against the schema below. It
must SKIP cleanly — not fail — when no pack.yaml exists yet, since real
packs are authored concurrently with this test.

Manifest schema (see ``tests/fixtures/skill_packs/demo-skill`` for a worked
example)::

    version: 1
    placeholders:                # every {{NAME}} token used by any item file
      - name: WORKSPACE_ENV      # must be declared here. ^[A-Z][A-Z0-9_]*$
        description: "..."       # required, non-empty
        example: "staging"       # required, non-empty string
    items:
      - kind: rule                        # rule | knowledge | command
        name: datadog-environment-boundary # ^[A-Za-z0-9_-]+$
        file: rules/datadog-environment-boundary.md   # relative to references/
      - kind: knowledge
        name: "Datadog MCP operating guide"  # KB title, <= 255 chars
        file: knowledge/datadog-operating-guide.md
        scope: global                        # global | resource (required)
      - kind: command
        name: datadog-morning-brief           # ^[A-Za-z0-9_-]+$
        file: commands/datadog-morning-brief.md
        description: "..."                    # required for command
        schedule:                             # optional
          cron: "0 8 * * 1-5"                # exactly 5 whitespace-separated fields

Size limits on raw file text: rule <= 1500 chars (rules are injected into
every model call), knowledge <= 20000 chars (papi NoteCreateV4 limit),
knowledge with ``scope: resource`` <= 3200 chars (agentfarm auto-inject
truncation), command <= 12000 chars.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from runwhen_platform_mcp.server import _skills_root

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

PLACEHOLDER_TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
ITEM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
VALID_KINDS = {"rule", "knowledge", "command"}
# Subdirectories scanned for orphaned (unreferenced) .md files, independent
# of what any individual item's ``file`` actually points at.
ORPHAN_SCAN_SUBDIRS = ("rules", "knowledge", "commands")
KIND_CHAR_LIMITS = {"rule": 1500, "knowledge": 20000, "command": 12000}
KNOWLEDGE_RESOURCE_SCOPE_LIMIT = 3200
KNOWLEDGE_NAME_MAX_LEN = 255


def validate_pack(pack_yaml_path: Path) -> list[str]:
    """Validate a single skill pack manifest.

    Returns a list of human-readable error strings, each naming the
    manifest and the offending item; an empty list means the manifest and
    every item file it references are valid.
    """
    pack_yaml_path = Path(pack_yaml_path)
    label = str(pack_yaml_path)
    errors: list[str] = []

    references_dir = pack_yaml_path.parent
    try:
        raw = pack_yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: could not read manifest: {exc}"]

    try:
        manifest = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"{label}: invalid YAML: {exc}"]

    if not isinstance(manifest, dict):
        return [f"{label}: manifest must be a YAML mapping"]

    if manifest.get("version") != 1:
        errors.append(f"{label}: 'version' must be 1, got {manifest.get('version')!r}")

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append(f"{label}: 'items' must be a non-empty list")
        items = []

    placeholders = manifest.get("placeholders")
    if placeholders is None:
        placeholders = []
    if not isinstance(placeholders, list):
        errors.append(f"{label}: 'placeholders' must be a list")
        placeholders = []

    declared_placeholders: dict[str, int] = {}
    for i, ph in enumerate(placeholders):
        ph_label = f"{label}: placeholders[{i}]"
        if not isinstance(ph, dict):
            errors.append(f"{ph_label}: must be a mapping")
            continue
        name = ph.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{ph_label}: 'name' is required")
            continue
        declared_placeholders[name] = declared_placeholders.get(name, 0) + 1
        description = ph.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{label}: placeholder {name!r} requires a non-empty 'description'")
        example = ph.get("example")
        if not isinstance(example, str) or not example.strip():
            errors.append(f"{label}: placeholder {name!r} requires a non-empty 'example'")

    for name, count in declared_placeholders.items():
        if count > 1:
            errors.append(f"{label}: placeholder {name!r} declared {count} times")

    seen_names: set[tuple[str, str]] = set()
    referenced_files: set[Path] = set()
    used_tokens: set[str] = set()

    for i, item in enumerate(items):
        item_label = f"{label}: items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be a mapping")
            continue

        kind = item.get("kind")
        name = item.get("name")

        if kind not in VALID_KINDS:
            errors.append(
                f"{item_label}: invalid kind {kind!r}; must be one of {sorted(VALID_KINDS)}"
            )
            continue

        if not isinstance(name, str) or not name:
            errors.append(f"{item_label} (kind={kind}): 'name' is required")
            continue

        # From here on, name a specific item in every message.
        item_label = f"{label}: item {name!r} ({kind})"

        key = (kind, name)
        if key in seen_names:
            errors.append(f"{label}: duplicate {kind} name {name!r}")
        seen_names.add(key)

        if kind in ("rule", "command") and not ITEM_NAME_RE.match(name):
            errors.append(f"{item_label}: name must match ^[A-Za-z0-9_-]+$, got {name!r}")
        if kind == "knowledge" and len(name) > KNOWLEDGE_NAME_MAX_LEN:
            errors.append(
                f"{item_label}: knowledge name is {len(name)} chars, "
                f"exceeds the {KNOWLEDGE_NAME_MAX_LEN}-char limit"
            )

        if kind == "knowledge":
            scope = item.get("scope")
            if scope not in ("global", "resource"):
                errors.append(
                    f"{item_label}: 'scope' must be 'global' or 'resource', got {scope!r}"
                )

        if kind == "command":
            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                errors.append(f"{item_label}: command requires a non-empty 'description'")
            schedule = item.get("schedule")
            if schedule is not None:
                if not isinstance(schedule, dict):
                    errors.append(f"{item_label}: 'schedule' must be a mapping")
                else:
                    cron = schedule.get("cron")
                    if not isinstance(cron, str) or len(cron.split()) != 5:
                        errors.append(
                            f"{item_label}: schedule.cron must have exactly 5 "
                            f"whitespace-separated fields, got {cron!r}"
                        )

        file_rel = item.get("file")
        if not isinstance(file_rel, str) or not file_rel:
            errors.append(f"{item_label}: 'file' is required")
            continue

        file_path = (references_dir / file_rel).resolve()
        referenced_files.add(file_path)

        if not file_path.is_file():
            errors.append(f"{item_label}: file {file_rel!r} does not exist under {references_dir}")
            continue

        text = file_path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append(f"{item_label}: file {file_rel!r} is empty")

        limit = KIND_CHAR_LIMITS[kind]
        if kind == "knowledge" and item.get("scope") == "resource":
            limit = KNOWLEDGE_RESOURCE_SCOPE_LIMIT
        if len(text) > limit:
            errors.append(
                f"{item_label}: file {file_rel!r} is {len(text)} chars, "
                f"exceeds the {limit}-char limit for kind {kind!r}"
            )

        used_tokens |= set(PLACEHOLDER_TOKEN_RE.findall(text))

    declared_names = set(declared_placeholders)
    for token in sorted(used_tokens - declared_names):
        errors.append(
            f"{label}: placeholder {{{{{token}}}}} used but not declared in 'placeholders'"
        )
    for name in sorted(declared_names - used_tokens):
        errors.append(f"{label}: placeholder {name!r} declared but never used by any item file")

    for subdir in ORPHAN_SCAN_SUBDIRS:
        subdir_path = references_dir / subdir
        if not subdir_path.is_dir():
            continue
        for md_file in sorted(subdir_path.rglob("*.md")):
            if md_file.resolve() not in referenced_files:
                errors.append(
                    f"{label}: orphan file "
                    f"{md_file.relative_to(references_dir)} is not referenced by any item"
                )

    return errors


# ---------------------------------------------------------------------------
# Repo discovery: validate every pack.yaml actually checked into skills/.
# Parametrizing over an empty list makes pytest skip the test with "got
# empty parameter set" rather than failing — exactly what we want while no
# pack.yaml has landed yet.
# ---------------------------------------------------------------------------


def _discovered_pack_manifests() -> list[Path]:
    return sorted(_skills_root().glob("*/references/pack.yaml"))


@pytest.mark.parametrize(
    "manifest_path",
    _discovered_pack_manifests(),
    ids=lambda p: p.parent.parent.name,
)
def test_repo_skill_pack_is_valid(manifest_path: Path) -> None:
    errors = validate_pack(manifest_path)
    assert errors == [], "\n".join(errors)


# ---------------------------------------------------------------------------
# Fixture-backed tests: prove validate_pack() works before any real pack
# lands, and that it actually catches broken manifests.
# ---------------------------------------------------------------------------

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "skill_packs"


def _copy_demo_pack(tmp_path: Path) -> Path:
    dest = tmp_path / "demo-skill"
    shutil.copytree(FIXTURES_ROOT / "demo-skill", dest)
    return dest


class TestValidatePackFixture:
    """Exercise ``validate_pack`` against a hand-built fixture pack."""

    def test_demo_fixture_is_valid(self) -> None:
        manifest = FIXTURES_ROOT / "demo-skill" / "references" / "pack.yaml"
        assert validate_pack(manifest) == []

    def test_undeclared_placeholder_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        knowledge_file = pack_dir / "references" / "knowledge" / "demo-knowledge.md"
        knowledge_file.write_text(knowledge_file.read_text() + "\n{{UNDECLARED_TOKEN}}\n")
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("UNDECLARED_TOKEN" in e for e in errors), errors

    def test_oversize_rule_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        rule_file = pack_dir / "references" / "rules" / "demo-rule.md"
        rule_file.write_text("x" * 1501)
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("1500" in e for e in errors), errors

    def test_missing_referenced_file_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        (pack_dir / "references" / "commands" / "demo-command.md").unlink()
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("does not exist" in e for e in errors), errors

    def test_orphan_file_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        (pack_dir / "references" / "rules" / "orphan-rule.md").write_text("An orphan rule.\n")
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("orphan" in e.lower() for e in errors), errors

    def test_bad_cron_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["items"][2]["schedule"]["cron"] = "0 8 * *"  # only 4 fields
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("cron" in e for e in errors), errors

    def test_duplicate_name_within_kind_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["items"].append(dict(manifest["items"][0]))  # duplicate the rule item
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("duplicate" in e.lower() for e in errors), errors
