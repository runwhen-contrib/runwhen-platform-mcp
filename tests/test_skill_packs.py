"""Generic validator + tests for skill "packs" (``references/pack.yaml``).

A pack manifest bundles the rules, knowledge articles, and commands a
workspace-tuning skill installs (e.g. ``configure-datadog-workspace``). This
file is deliberately generic: it discovers every
``skills/*/references/pack.yaml`` under the repo's skills root (via
``_skills_root()``) and validates each one against the schema below. It
must SKIP cleanly — not fail — when no pack.yaml exists yet, since real
packs are authored concurrently with this test.

Packs must need zero user input: there are no placeholders, and every pack
declares which MCP servers it applies to (by catalog id and/or endpoint
host) so an installer can decide whether to install it without asking the
user anything.

Manifest schema (see ``tests/fixtures/skill_packs/demo-skill`` for a worked
example)::

    version: 1
    id: datadog                      # ^[a-z0-9][a-z0-9-]*$
    title: Datadog MCP               # non-empty string
    description: "..."               # non-empty string
    match:                           # at least one list present and non-empty
      mcp_catalog_ids: [datadog]     # each ^[a-z0-9][a-z0-9-]*$
      mcp_endpoint_hosts:            # bare hostnames: has a dot, no "://",
        - mcp.datadoghq.com          # no "/", no spaces, lowercase
    items:
      - kind: rule                        # rule | knowledge | command
        name: datadog-mcp                 # ^[A-Za-z0-9_-]+$
        file: rules/datadog-mcp.md         # relative to references/
      - kind: knowledge
        name: datadog-mcp-operating-guide   # KB title: hyphen-separated slug, <= 255 chars
        file: knowledge/datadog-operating-guide.md
      - kind: command
        name: datadog-morning-brief           # ^[A-Za-z0-9_-]+$
        file: commands/datadog-morning-brief.md
        description: "..."                    # required for command

No other top-level or item keys are allowed. Size limits on raw file text:
rule <= 1500 chars (rules are injected into every model call), knowledge <=
20000 chars (papi NoteCreateV4 limit), command <= 12000 chars. No item file
may contain a ``{{...}}`` template token — packs must need no user input.
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

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ITEM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TEMPLATE_TOKEN_RE = re.compile(r"\{\{[^}]*\}\}")
VALID_KINDS = {"rule", "knowledge", "command"}
# Subdirectories scanned for orphaned (unreferenced) .md files, independent
# of what any individual item's ``file`` actually points at.
ORPHAN_SCAN_SUBDIRS = ("rules", "knowledge", "commands")
KIND_CHAR_LIMITS = {"rule": 1500, "knowledge": 20000, "command": 12000}
KNOWLEDGE_NAME_MAX_LEN = 255
# PAPI note titles must be hyphen-separated slugs (shared/services/sync/note_sync.py
# TITLE_RE in 468-platform); the title is also the /.runwhen/knowledge/<title> path.
KNOWLEDGE_TITLE_RE = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")
ALLOWED_TOP_LEVEL_KEYS = {"version", "id", "title", "description", "match", "items"}
ALLOWED_MATCH_KEYS = {"mcp_catalog_ids", "mcp_endpoint_hosts"}
BASE_ITEM_KEYS = {"kind", "name", "file"}
ITEM_KEYS_BY_KIND = {
    "rule": BASE_ITEM_KEYS,
    "knowledge": BASE_ITEM_KEYS,
    "command": BASE_ITEM_KEYS | {"description"},
}


def _is_bare_hostname(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "." in value
        and "://" not in value
        and "/" not in value
        and " " not in value
        and value == value.lower()
    )


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

    unknown_top_level_keys = set(manifest) - ALLOWED_TOP_LEVEL_KEYS
    if "placeholders" in unknown_top_level_keys:
        errors.append(f"{label}: packs must not declare placeholders")
        unknown_top_level_keys.discard("placeholders")
    for key in sorted(unknown_top_level_keys):
        errors.append(f"{label}: unknown top-level key {key!r}")

    if manifest.get("version") != 1:
        errors.append(f"{label}: 'version' must be 1, got {manifest.get('version')!r}")

    pack_id = manifest.get("id")
    if not isinstance(pack_id, str) or not ID_RE.match(pack_id):
        errors.append(f"{label}: 'id' must match ^[a-z0-9][a-z0-9-]*$, got {pack_id!r}")

    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{label}: 'title' is required and must be a non-empty string")

    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{label}: 'description' is required and must be a non-empty string")

    match = manifest.get("match")
    if not isinstance(match, dict):
        errors.append(f"{label}: 'match' is required and must be a mapping")
        match = {}
    else:
        for key in sorted(set(match) - ALLOWED_MATCH_KEYS):
            errors.append(f"{label}: unknown key {key!r} in 'match'")

    catalog_ids = match.get("mcp_catalog_ids")
    if catalog_ids is None:
        catalog_ids = []
    elif not isinstance(catalog_ids, list):
        errors.append(f"{label}: 'match.mcp_catalog_ids' must be a list")
        catalog_ids = []
    else:
        for i, catalog_id in enumerate(catalog_ids):
            if not isinstance(catalog_id, str) or not ID_RE.match(catalog_id):
                errors.append(
                    f"{label}: match.mcp_catalog_ids[{i}] must match "
                    f"^[a-z0-9][a-z0-9-]*$, got {catalog_id!r}"
                )

    endpoint_hosts = match.get("mcp_endpoint_hosts")
    if endpoint_hosts is None:
        endpoint_hosts = []
    elif not isinstance(endpoint_hosts, list):
        errors.append(f"{label}: 'match.mcp_endpoint_hosts' must be a list")
        endpoint_hosts = []
    else:
        for i, host in enumerate(endpoint_hosts):
            if not _is_bare_hostname(host):
                errors.append(
                    f"{label}: match.mcp_endpoint_hosts[{i}] must be a bare lowercase "
                    f"hostname (no scheme, path, or spaces), got {host!r}"
                )

    if not catalog_ids and not endpoint_hosts:
        errors.append(
            f"{label}: 'match' must declare at least one non-empty 'mcp_catalog_ids' "
            "or 'mcp_endpoint_hosts' list"
        )

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append(f"{label}: 'items' must be a non-empty list")
        items = []

    seen_names: set[tuple[str, str]] = set()
    referenced_files: set[Path] = set()

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

        allowed_keys = ITEM_KEYS_BY_KIND[kind]
        for key in sorted(set(item) - allowed_keys):
            errors.append(f"{item_label}: unknown key {key!r}")

        key = (kind, name)
        if key in seen_names:
            errors.append(f"{label}: duplicate {kind} name {name!r}")
        seen_names.add(key)

        if kind in ("rule", "command") and not ITEM_NAME_RE.match(name):
            errors.append(f"{item_label}: name must match ^[A-Za-z0-9_-]+$, got {name!r}")
        if kind == "knowledge" and not KNOWLEDGE_TITLE_RE.match(name):
            errors.append(
                f"{item_label}: knowledge name must be a hyphen-separated slug "
                f"(e.g. 'datadog-mcp-operating-guide'), got {name!r}"
            )
        if kind == "knowledge" and len(name) > KNOWLEDGE_NAME_MAX_LEN:
            errors.append(
                f"{item_label}: knowledge name is {len(name)} chars, "
                f"exceeds the {KNOWLEDGE_NAME_MAX_LEN}-char limit"
            )

        if kind == "command":
            command_description = item.get("description")
            if not isinstance(command_description, str) or not command_description.strip():
                errors.append(f"{item_label}: command requires a non-empty 'description'")

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
        if len(text) > limit:
            errors.append(
                f"{item_label}: file {file_rel!r} is {len(text)} chars, "
                f"exceeds the {limit}-char limit for kind {kind!r}"
            )

        if TEMPLATE_TOKEN_RE.search(text):
            errors.append(
                f"{item_label}: file {file_rel!r} contains a {{{{...}}}} template token; "
                "packs must need no user input"
            )

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

    def test_template_token_in_file_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        knowledge_file = pack_dir / "references" / "knowledge" / "demo-knowledge.md"
        knowledge_file.write_text(knowledge_file.read_text() + "\n{{SOME_TOKEN}}\n")
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("template token" in e for e in errors), errors

    def test_placeholders_key_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["placeholders"] = []
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("packs must not declare placeholders" in e for e in errors), errors

    def test_missing_match_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        del manifest["match"]
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("match" in e for e in errors), errors

    def test_match_with_both_lists_empty_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["match"] = {"mcp_catalog_ids": [], "mcp_endpoint_hosts": []}
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("match" in e for e in errors), errors

    def test_bad_hostname_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["match"]["mcp_endpoint_hosts"] = ["https://mcp.x.com"]
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("hostname" in e for e in errors), errors

    def test_unknown_item_key_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["items"][0]["schedule"] = {"cron": "0 8 * * 1-5"}
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("schedule" in e for e in errors), errors

    def test_oversize_rule_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        rule_file = pack_dir / "references" / "rules" / "demo-rule.md"
        rule_file.write_text("x" * 1501)
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("1500" in e for e in errors), errors

    def test_orphan_file_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        (pack_dir / "references" / "rules" / "orphan-rule.md").write_text("An orphan rule.\n")
        errors = validate_pack(pack_dir / "references" / "pack.yaml")
        assert any("orphan" in e.lower() for e in errors), errors

    def test_duplicate_name_within_kind_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["items"].append(dict(manifest["items"][0]))  # duplicate the rule item
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("duplicate" in e.lower() for e in errors), errors

    def test_knowledge_name_must_be_a_slug(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["items"][1]["name"] = "Demo Knowledge Article"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("slug" in e for e in errors), errors

    def test_command_missing_description_is_an_error(self, tmp_path: Path) -> None:
        pack_dir = _copy_demo_pack(tmp_path)
        manifest_path = pack_dir / "references" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        del manifest["items"][2]["description"]
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        errors = validate_pack(manifest_path)
        assert any("description" in e for e in errors), errors
