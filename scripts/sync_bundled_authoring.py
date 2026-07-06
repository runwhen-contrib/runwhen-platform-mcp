#!/usr/bin/env python3
"""Copy runwhen-local authoring docs into the MCP skill bundle (airgap-safe).

Build-time / CI only — the MCP server never fetches these from the network.
Agents read ``skills/author-generation-rules/references/`` via ``get_skill`` and
local file reads from the installed package.

Source: runwhen-contrib/runwhen-local checkout (adjacent path or --runwhen-local).

Usage::

    python scripts/sync_bundled_authoring.py
    python scripts/sync_bundled_authoring.py --check
    python scripts/sync_bundled_authoring.py --runwhen-local /path/to/runwhen-local
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RWL = _REPO_ROOT.parent / "runwhen-local"
_BUNDLE_ROOT = _REPO_ROOT / "skills" / "author-generation-rules" / "references"

# (source relative to runwhen-local root, dest relative to references/)
_COPY_FILES: list[tuple[str, str]] = [
    ("docs/authoring/concepts.md", "concepts.md"),
    ("docs/authoring/generation-rules/README.md", "generation-rules-schema.md"),
    ("generation-rules-guide.md", "generation-rules-syntax.md"),
    (
        "docs/authoring/generation-rules/tag-hierarchy-contract.md",
        "tag-hierarchy-contract.md",
    ),
    ("docs/authoring/indexed-resources/README.md", "indexed-resources-index.md"),
    ("docs/authoring/indexed-resources/azure.md", "indexed-resources/azure.md"),
    ("docs/authoring/indexed-resources/aws.md", "indexed-resources/aws.md"),
    ("docs/authoring/indexed-resources/gcp.md", "indexed-resources/gcp.md"),
    (
        "docs/authoring/indexed-resources/kubernetes.md",
        "indexed-resources/kubernetes.md",
    ),
    (
        "docs/authoring/indexed-resources/runwhen-platform.md",
        "indexed-resources/runwhen-platform.md",
    ),
    (
        "docs/authoring/indexed-resources/azure-resource-catalog.md",
        "catalogs/azure-resource-catalog.md",
    ),
    (
        "docs/authoring/indexed-resources/aws-resource-catalog.md",
        "catalogs/aws-resource-catalog.md",
    ),
    (
        "docs/authoring/indexed-resources/gcp-resource-catalog.md",
        "catalogs/gcp-resource-catalog.md",
    ),
    (
        "docs/authoring/indexed-resources/kubernetes-resource-catalog.md",
        "catalogs/kubernetes-resource-catalog.md",
    ),
    (
        "docs/authoring/indexed-resources/runwhen-platform-resource-catalog.md",
        "catalogs/runwhen-platform-resource-catalog.md",
    ),
    ("src/generation-rule-schema.json", "generation-rule-schema.json"),
]

_COPY_DIRS: list[tuple[str, str]] = [
    ("docs/authoring/generation-rules/examples", "examples"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync(runwhen_local: Path, check: bool = False) -> int:
    if not runwhen_local.is_dir():
        print(f"ERROR: runwhen-local not found at {runwhen_local}", file=sys.stderr)
        return 1

    drift = 0
    manifest_entries: list[dict[str, str]] = []

    for src_rel, dest_rel in _COPY_FILES:
        src = runwhen_local / src_rel
        dest = _BUNDLE_ROOT / dest_rel
        if not src.is_file():
            print(f"ERROR: missing source {src}", file=sys.stderr)
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_bytes()
        if dest.exists() and dest.read_bytes() == content:
            print(f"OK   {dest_rel}")
        elif check:
            print(f"DRIFT {dest_rel}")
            drift += 1
        else:
            dest.write_bytes(content)
            print(f"WROTE {dest_rel}")
        if check and not dest.exists():
            continue
        manifest_entries.append({"path": dest_rel, "sha256": _sha256(dest)})

    for src_dir_rel, dest_dir_rel in _COPY_DIRS:
        src_dir = runwhen_local / src_dir_rel
        dest_dir = _BUNDLE_ROOT / dest_dir_rel
        if not src_dir.is_dir():
            print(f"ERROR: missing source dir {src_dir}", file=sys.stderr)
            return 1
        if dest_dir.exists() and not check:
            shutil.rmtree(dest_dir)
        for src_file in sorted(src_dir.glob("*.md")):
            rel = f"{dest_dir_rel}/{src_file.name}"
            dest = _BUNDLE_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = src_file.read_bytes()
            if dest.exists() and dest.read_bytes() == content:
                print(f"OK   {rel}")
            elif check:
                print(f"DRIFT {rel}")
                drift += 1
            else:
                dest.write_bytes(content)
                print(f"WROTE {rel}")
            if check and not dest.exists():
                continue
            manifest_entries.append({"path": rel, "sha256": _sha256(dest)})

    manifest = {
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runwhen_local": str(runwhen_local.resolve()),
        "files": manifest_entries,
    }
    manifest_path = _BUNDLE_ROOT / "BUNDLE_MANIFEST.json"
    if check:
        if not manifest_path.is_file():
            print("DRIFT BUNDLE_MANIFEST.json (missing)")
            drift += 1
        else:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("files") != manifest_entries:
                print("DRIFT BUNDLE_MANIFEST.json (file list changed)")
                drift += 1
            else:
                print("OK   BUNDLE_MANIFEST.json")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("WROTE BUNDLE_MANIFEST.json")

    readme = _BUNDLE_ROOT / "README.md"
    readme_text = (
        "# Bundled authoring reference (airgap)\n\n"
        "Copied from runwhen-local by `scripts/sync_bundled_authoring.py`.\n"
        "Do not hand-edit — regenerate from runwhen-local.\n\n"
        "| File | Purpose |\n"
        "| --- | --- |\n"
        "| `generation-rules-schema.md` | GenerationRules YAML schema |\n"
        "| `generation-rules-syntax.md` | Full matchRules / slxs reference |\n"
        "| `generation-rule-schema.json` | JSON Schema for validation |\n"
        "| `catalogs/*` | Indexer resource type catalogs |\n"
        "| `examples/*` | End-to-end generation rule examples |\n"
        "| `indexed-resources/*` | Per-platform indexer guides |\n"
    )
    if not check:
        readme.write_text(readme_text, encoding="utf-8")

    return 1 if drift else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runwhen-local",
        type=Path,
        default=_DEFAULT_RWL,
        help=f"Path to runwhen-local checkout (default: {_DEFAULT_RWL})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if bundle differs from runwhen-local sources",
    )
    args = parser.parse_args()
    raise SystemExit(sync(args.runwhen_local, check=args.check))


if __name__ == "__main__":
    main()
