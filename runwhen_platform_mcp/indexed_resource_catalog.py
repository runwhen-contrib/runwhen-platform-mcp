"""Bundled indexer resource-type catalogs (airgap-safe, offline-only).

Validates ``resourceTypes`` in ``render_codecollection_skill`` against catalogs
shipped under ``skills/author-generation-rules/references/``. Prefers the compact
``catalogs/indexed-resource-types.json`` index (generated at bundle sync time)
and falls back to parsing markdown when the index is absent.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from runwhen_platform_mcp.bundled_skills import authoring_references_root, is_airgap_mode
from runwhen_platform_mcp.codecollection_platform_profiles import SUPPORTED_PLATFORMS

_INDEX_JSON_NAME = "indexed-resource-types.json"
_CATALOG_DIR_NAME = "catalogs"

# Cloud catalogs are large; unfiltered list responses are noisy for agents.
_LARGE_CATALOG_PLATFORMS: Final[frozenset[str]] = frozenset({"azure", "aws", "gcp"})
_MIN_SEARCH_LEN_LARGE_CATALOG: Final[int] = 2

# Kubernetes CRD resourceTypes: plural.group[/version]
_K8S_CRD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9.-]*)+(/v[a-z0-9]+(alpha[0-9]*)?(beta[0-9]*)?)?$"
)

_K8S_BUILTIN_FROM_DOCS: Final[frozenset[str]] = frozenset(
    {
        "cluster",
        "namespace",
        "deployment",
        "statefulset",
        "daemonset",
        "pod",
        "service",
        "configmap",
        "secret",
        "cronjob",
        "job",
        "ingress",
        "gatewayclass",
        "gateway",
        "httproute",
        "custom",
        "persistentvolumeclaim",
    }
)


def _catalog_dir() -> Path:
    return authoring_references_root() / _CATALOG_DIR_NAME


def _index_json_path() -> Path:
    return _catalog_dir() / _INDEX_JSON_NAME


def _catalog_markdown_path(platform: str) -> Path:
    from runwhen_platform_mcp.codecollection_platform_profiles import get_platform_profile

    profile = get_platform_profile(platform)
    return _catalog_dir() / profile.catalog_doc_path.removeprefix(f"{_CATALOG_DIR_NAME}/")


def bundled_catalog_doc_path(platform: str) -> str:
    """Skill-relative path to the markdown catalog (always offline)."""
    from runwhen_platform_mcp.codecollection_platform_profiles import get_platform_profile

    return get_platform_profile(platform).catalog_doc_path


def catalog_reference_for_agents(platform: str) -> dict[str, str | None]:
    """Airgap-safe pointers for tool responses and validation errors."""
    skill_path = f"author-generation-rules/references/{bundled_catalog_doc_path(platform)}"
    result: dict[str, str | None] = {
        "catalog_source": "bundled",
        "bundled_catalog_path": skill_path,
        "bundled_index_path": (
            f"author-generation-rules/references/{_CATALOG_DIR_NAME}/{_INDEX_JSON_NAME}"
        ),
        "get_skill_hint": "get_skill('author-generation-rules')",
    }
    if not is_airgap_mode():
        docs_slug = "runwhen-platform" if platform == "runwhen" else platform
        result["indexed_resources_url"] = (
            f"https://docs.runwhen.com/authors/indexed-resources/{docs_slug}/"
        )
    else:
        result["indexed_resources_url"] = None
        result["airgap_note"] = (
            "Catalog is bundled with the MCP package — no network access required. "
            "Use list_indexed_resource_types(search=...) or get_skill('author-generation-rules')."
        )
    return result


def _parse_kubernetes_catalog(text: str) -> frozenset[str]:
    found: set[str] = set()
    for match in re.finditer(r"\|\s*`([^`]+)`\s*\|", text):
        name = match.group(1).strip().lower()
        if name and not name.startswith("http"):
            found.add(name)
    return frozenset(found) | _K8S_BUILTIN_FROM_DOCS


def _parse_cloud_catalog(text: str) -> frozenset[str]:
    """Extract CloudQuery table names from azure/aws/gcp catalog markdown tables."""
    found: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 4:
            continue
        cell = parts[2]
        tick = re.search(r"`([^`]+)`", cell)
        if tick:
            found.add(tick.group(1).strip().lower())
    return frozenset(found)


def _parse_runwhen_catalog(text: str) -> frozenset[str]:
    found: set[str] = set()
    for match in re.finditer(r"\|\s*`([^`]+)`\s*\|", text):
        found.add(match.group(1).strip().lower())
    return frozenset(found) or frozenset({"workspace"})


def _parse_catalog_markdown(platform: str) -> frozenset[str]:
    key = platform.strip().lower()
    path = _catalog_markdown_path(key)
    if not path.is_file():
        if key == "kubernetes":
            return _K8S_BUILTIN_FROM_DOCS
        if key == "runwhen":
            return frozenset({"workspace"})
        return frozenset()

    text = path.read_text(encoding="utf-8")
    if key == "kubernetes":
        return _parse_kubernetes_catalog(text)
    if key == "runwhen":
        return _parse_runwhen_catalog(text)
    return _parse_cloud_catalog(text)


def _load_index_json() -> dict[str, list[str]] | None:
    path = _index_json_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    normalized: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, list):
            normalized[key.lower()] = [str(item).lower() for item in value]
    return normalized or None


@lru_cache(maxsize=1)
def _index_json_cache() -> dict[str, list[str]] | None:
    return _load_index_json()


@lru_cache(maxsize=len(SUPPORTED_PLATFORMS))
def get_known_resource_types(platform: str) -> frozenset[str]:
    """Return indexer-known resource type names for *platform*."""
    key = platform.strip().lower()
    index = _index_json_cache()
    if index is not None and key in index:
        return frozenset(index[key])
    return _parse_catalog_markdown(key)


def build_resource_type_index(references_root: Path | None = None) -> dict[str, list[str]]:
    """Build sorted resource-type lists for all platforms (sync/CI helper)."""
    root = references_root or authoring_references_root()
    catalog_dir = root / _CATALOG_DIR_NAME
    result: dict[str, list[str]] = {}
    for platform in sorted(SUPPORTED_PLATFORMS):
        from runwhen_platform_mcp.codecollection_platform_profiles import get_platform_profile

        profile = get_platform_profile(platform)
        md_path = catalog_dir / profile.catalog_doc_path.removeprefix(f"{_CATALOG_DIR_NAME}/")
        if not md_path.is_file():
            if platform == "kubernetes":
                types = sorted(_K8S_BUILTIN_FROM_DOCS)
            elif platform == "runwhen":
                types = ["workspace"]
            else:
                types = []
        else:
            text = md_path.read_text(encoding="utf-8")
            if platform == "kubernetes":
                types = sorted(_parse_kubernetes_catalog(text))
            elif platform == "runwhen":
                types = sorted(_parse_runwhen_catalog(text))
            else:
                types = sorted(_parse_cloud_catalog(text))
        result[platform] = types
    return result


def write_resource_type_index(references_root: Path | None = None) -> Path:
    """Write ``indexed-resource-types.json`` next to markdown catalogs."""
    root = references_root or authoring_references_root()
    payload = build_resource_type_index(root)
    out = root / _CATALOG_DIR_NAME / _INDEX_JSON_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    get_known_resource_types.cache_clear()
    _index_json_cache.cache_clear()
    return out


def is_kubernetes_crd_resource_type(resource_type: str) -> bool:
    """True when *resource_type* looks like ``plural.group[/version]``."""
    return bool(_K8S_CRD_PATTERN.match(resource_type.strip().lower()))


def validate_resource_types(platform: str, resource_types: list[str]) -> list[str]:
    """Return human-readable errors; empty list means all types are valid."""
    if not resource_types:
        return ["resource_types must include at least one indexed resource type."]

    key = platform.strip().lower()
    known = get_known_resource_types(key)
    ref = catalog_reference_for_agents(key)
    errors: list[str] = []

    for raw in resource_types:
        name = raw.strip().lower()
        if not name:
            errors.append("resource_types entries must be non-empty strings.")
            continue
        if key == "kubernetes":
            if name in known or is_kubernetes_crd_resource_type(name):
                continue
            errors.append(
                f"Unknown kubernetes resource type {raw!r}. "
                f"Use list_indexed_resource_types(platform='kubernetes', search='...') "
                f"or a CRD in plural.group[/version] form declared in "
                f"workspaceInfo customResourceTypes. "
                f"Bundled catalog: {ref['bundled_catalog_path']}."
            )
            continue
        if name not in known:
            errors.append(
                f"Unknown {key} resource type {raw!r}. "
                f"Call list_indexed_resource_types(platform={key!r}, search=...) "
                f"or read {ref['bundled_catalog_path']} via get_skill('author-generation-rules')."
            )
    return errors


def search_resource_types(platform: str, search: str = "", limit: int = 50) -> list[str]:
    """Return catalog entries matching *search* (substring), capped at *limit*."""
    known = sorted(get_known_resource_types(platform))
    needle = search.strip().lower()
    if not needle:
        return known[:limit]
    matches = [name for name in known if needle in name]
    return matches[:limit]


def list_resource_types_response(
    platform: str,
    search: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Build the MCP tool payload for ``list_indexed_resource_types``."""
    key = platform.strip().lower()
    total = len(get_known_resource_types(key))
    ref = catalog_reference_for_agents(key)
    needle = search.strip()

    if key in _LARGE_CATALOG_PLATFORMS and len(needle) < _MIN_SEARCH_LEN_LARGE_CATALOG:
        return {
            "platform": key,
            "search": search,
            "total_catalog_entries": total,
            "returned_count": 0,
            "resource_types": [],
            "search_required": True,
            "message": (
                f"The {key} catalog has {total} resource types. "
                f"Provide search= with at least {_MIN_SEARCH_LEN_LARGE_CATALOG} characters "
                f"(e.g. search='{key}_keyvault' or search='compute'). "
                "Lookup is fully offline from the bundled index — no network needed."
            ),
            **ref,
        }

    matches = search_resource_types(key, search=needle, limit=limit)
    truncated = total > len(matches) and (needle or total > limit)
    payload: dict[str, Any] = {
        "platform": key,
        "search": search,
        "total_catalog_entries": total,
        "returned_count": len(matches),
        "resource_types": matches,
        "search_required": False,
        **ref,
    }
    if truncated:
        payload["hint"] = (
            "Results truncated — refine search= or increase limit=. "
            "Catalog data is bundled; no external network required."
        )
    if key == "kubernetes":
        payload["kubernetes_crd_note"] = (
            "CRD resourceTypes use plural.group[/version] and must be declared "
            "in workspaceInfo customResourceTypes."
        )
    return payload


def validate_qualifiers(platform: str, qualifiers: list[str]) -> list[str]:
    """Return errors for SLX qualifier names incompatible with *platform*."""
    from runwhen_platform_mcp.codecollection_platform_profiles import get_platform_profile

    if not qualifiers:
        return ["slx_qualifiers must include at least one qualifier."]

    profile = get_platform_profile(platform)
    errors: list[str] = []
    for raw in qualifiers:
        name = raw.strip().lower()
        if name not in profile.valid_qualifiers:
            allowed = ", ".join(sorted(profile.valid_qualifiers))
            errors.append(
                f"Invalid qualifier {raw!r} for platform {platform!r}. Allowed: {allowed}."
            )
    return errors
