"""Render Custom Discovery CodeCollection files from MCP tool-builder output.

Emits the layout documented at https://docs.runwhen.com/guides/custom-discovery-codecollection/
with ``platform: runwhen`` generation rules for workspace-scoped tool-builder tasks.
"""

from __future__ import annotations

import base64
import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

MCP_PACKAGE_VERSION = "0.0.0-dev"

DEFAULT_GENERIC_REPO = "https://github.com/runwhen-contrib/rw-generic-codecollection.git"
DEFAULT_GENERIC_REF = "main"
TOOL_BUILDER_RUNBOOK_PATH = "codebundles/tool-builder/runbook.robot"
TOOL_BUILDER_SLI_PATH = "codebundles/tool-builder/sli.robot"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass
class CodecollectionRenderInput:
    """Inputs for rendering a private codecollection bundle."""

    bundle_name: str
    alias: str
    statement: str
    task_title: str
    script: str
    interpreter: str = "python"
    env_vars: dict[str, str] | None = None
    secret_vars: dict[str, str] | None = None
    runtime_vars: list[dict[str, Any]] | None = None
    interval_seconds: int = 300
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    tags: list[dict[str, str]] | None = None
    access: str = "read-write"
    data: str = "logs-bulk"
    image_url: str | None = None
    owners: list[str] | None = None
    generic_repo_url: str = DEFAULT_GENERIC_REPO
    generic_ref: str = DEFAULT_GENERIC_REF
    platform: str = "runwhen"
    resource_types: list[str] = field(default_factory=lambda: ["workspace"])
    match_rules: list[dict[str, Any]] | None = None
    slx_qualifiers: list[str] = field(default_factory=lambda: ["workspace"])
    base_name: str | None = None
    include_sli: bool = False
    sli_script: str | None = None
    sli_interpreter: str | None = None
    sli_interval_seconds: int = 300
    source_workspace: str | None = None
    source_slx_name: str | None = None
    mcp_version: str = MCP_PACKAGE_VERSION


def _default_match_rules() -> list[dict[str, Any]]:
    return [
        {
            "type": "pattern",
            "pattern": ".+",
            "properties": ["name"],
            "mode": "substring",
        }
    ]


def _resolve_base_name(bundle_name: str, base_name: str | None) -> str:
    if base_name:
        return base_name
    trimmed = bundle_name[:15].rstrip("-")
    return trimmed or "task"


def _tag_lines(tags: list[dict[str, str]] | None, access: str, data: str) -> list[str]:
    merged: list[dict[str, str]] = [{"name": "platform", "value": "runwhen"}]
    for tag in tags or []:
        if tag.get("name") not in ("access", "data", "platform"):
            merged.append(tag)
    merged.append({"name": "access", "value": access})
    merged.append({"name": "data", "value": data})
    lines: list[str] = []
    for tag in merged:
        lines.append(f"            - name: {tag['name']}")
        lines.append(f"              value: {tag['value']}")
    return lines


def _yaml_quote(value: str) -> str:
    raw = yaml.dump(value, default_flow_style=True, width=1_000_000)
    # yaml.dump emits a trailing '...\n' document-end marker — remove it
    if raw.endswith("...\n"):
        raw = raw[:-4]
    elif raw.endswith("..."):
        raw = raw[:-3]
    return raw.rstrip("\n")


def _build_generation_rule_yaml(inp: CodecollectionRenderInput, base_name: str) -> str:
    match_rules = inp.match_rules if inp.match_rules is not None else _default_match_rules()
    output_items: list[dict[str, str]] = [{"type": "slx"}]
    if inp.include_sli:
        output_items.append({"type": "sli"})
    output_items.append(
        {
            "type": "runbook",
            "templateName": f"{inp.bundle_name}-taskset.yaml",
        }
    )
    doc = {
        "apiVersion": "runwhen.com/v1",
        "kind": "GenerationRules",
        "spec": {
            "platform": inp.platform,
            "generationRules": [
                {
                    "resourceTypes": list(inp.resource_types),
                    "matchRules": match_rules,
                    "slxs": [
                        {
                            "baseName": base_name,
                            "qualifiers": list(inp.slx_qualifiers),
                            "baseTemplateName": inp.bundle_name,
                            "levelOfDetail": "detailed",
                            "outputItems": output_items,
                        }
                    ],
                }
            ],
        },
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _build_slx_template(inp: CodecollectionRenderInput) -> str:
    tag_lines = "\n".join(_tag_lines(inp.tags, inp.access, inp.data))
    image_url = inp.image_url or (
        "https://storage.googleapis.com/runwhen-nonprod-shared-images/icons/runwhen.svg"
    )
    alias = inp.alias.replace('"', '\\"')
    statement = inp.statement.replace("\n", " ")
    return textwrap.dedent(
        f"""\
        apiVersion: runwhen.com/v1
        kind: ServiceLevelX
        metadata:
          name: {{{{slx_name}}}}
          labels:
            {{% include "common-labels.yaml" %}}
          annotations:
            {{% include "common-annotations.yaml" %}}
        spec:
          imageURL: {image_url}
          alias: {alias}
          asMeasuredBy: >-
            Tool Builder task via rw-generic-codecollection tool-builder runtime.
          owners:
          - {{{{workspace.owner_email}}}}
          statement: >-
            {statement}
          additionalContext:
            qualified_name: "{{{{ match_resource.qualified_name }}}}"
          tags:
{tag_lines}
        """
    )


def _config_provided_lines(
    inp: CodecollectionRenderInput,
    script_b64: str,
    *,
    include_task_title: bool,
    interpreter: str | None = None,
) -> list[str]:
    env_vars = inp.env_vars or {}
    secret_vars = inp.secret_vars or {}
    resolved_interpreter = interpreter or inp.interpreter
    lines: list[str] = []
    if include_task_title:
        lines.append("            - name: TASK_TITLE")
        lines.append(f"              value: {_yaml_quote(inp.task_title)}")
    lines.append("            - name: GEN_CMD")
    lines.append(f"              value: {_yaml_quote(script_b64)}")
    lines.append("            - name: INTERPRETER")
    lines.append(f"              value: {_yaml_quote(resolved_interpreter)}")
    lines.append("            - name: CONFIG_ENV_MAP")
    lines.append(f"              value: {_yaml_quote(json.dumps(env_vars))}")
    lines.append("            - name: SECRET_ENV_MAP")
    lines.append(f"              value: {_yaml_quote(json.dumps(list(secret_vars.keys())))}")
    lines.append("            - name: TIMEOUT_SECONDS")
    lines.append(f"              value: {_yaml_quote(str(inp.timeout_seconds))}")
    for key, value in env_vars.items():
        lines.append(f"            - name: {key}")
        lines.append(f"              value: {_yaml_quote(value)}")
    return lines


def _secrets_provided_lines(secret_vars: dict[str, str] | None) -> list[str]:
    if not secret_vars:
        return []
    lines = ["          secretsProvided:"]
    for name, workspace_key in secret_vars.items():
        lines.append(f"            - name: {name}")
        lines.append(f"              workspaceKey: {workspace_key}")
    return lines


def _runtime_vars_lines(runtime_vars: list[dict[str, Any]] | None) -> list[str]:
    if not runtime_vars:
        return []
    lines = ["          runtimeVarsProvided:"]
    for rv in runtime_vars:
        lines.append("            - name: " + rv["name"])
        lines.append("              default: " + _yaml_quote(rv.get("default", "")))
        lines.append("              description: " + _yaml_quote(rv.get("description", "")))
        validation = rv.get("validation") or {}
        lines.append("              validation:")
        lines.append("                type: " + _yaml_quote(str(validation.get("type", "regex"))))
        if validation.get("pattern"):
            lines.append("                pattern: " + _yaml_quote(str(validation["pattern"])))
        if validation.get("values"):
            lines.append("                values: " + json.dumps(validation["values"]))
    return lines


def _build_taskset_template(inp: CodecollectionRenderInput, script_b64: str) -> str:
    config_lines = "\n".join(_config_provided_lines(inp, script_b64, include_task_title=True))
    secret_lines = _secrets_provided_lines(inp.secret_vars)
    runtime_lines = _runtime_vars_lines(inp.runtime_vars)
    secret_block = "\n".join(secret_lines) if secret_lines else ""
    runtime_block = "\n".join(runtime_lines) if runtime_lines else ""
    return textwrap.dedent(
        f"""\
        apiVersion: runwhen.com/v1
        kind: Runbook
        metadata:
          name: {{{{slx_name}}}}
          labels:
            {{% include "common-labels.yaml" %}}
          annotations:
            {{% include "common-annotations.yaml" %}}
        spec:
          location: {{{{default_location}}}}
          codeBundle:
            repoUrl: {inp.generic_repo_url}
            ref: {inp.generic_ref}
            pathToRobot: {TOOL_BUILDER_RUNBOOK_PATH}
          configProvided:
{config_lines}
{secret_block}
{runtime_block}
        """
    )


def _build_sli_template(inp: CodecollectionRenderInput, script_b64: str) -> str:
    sli_interpreter = inp.sli_interpreter or inp.interpreter
    config_lines = "\n".join(
        _config_provided_lines(
            inp, script_b64, include_task_title=False, interpreter=sli_interpreter
        )
    )
    secret_lines = _secrets_provided_lines(inp.secret_vars)
    secret_block = "\n".join(secret_lines) if secret_lines else ""
    return textwrap.dedent(
        f"""\
        apiVersion: runwhen.com/v1
        kind: ServiceLevelIndicator
        metadata:
          name: {{{{slx_name}}}}
          labels:
            {{% include "common-labels.yaml" %}}
          annotations:
            {{% include "common-annotations.yaml" %}}
        spec:
          displayUnitsLong: OK
          displayUnitsShort: ok
          locations:
            - {{{{default_location}}}}
          description: >-
            Tool Builder SLI for {inp.alias}
          codeBundle:
            repoUrl: {inp.generic_repo_url}
            ref: {inp.generic_ref}
            pathToRobot: {TOOL_BUILDER_SLI_PATH}
          intervalStrategy: intermezzo
          intervalSeconds: {inp.sli_interval_seconds}
          configProvided:
{config_lines}
{secret_block}
          alertConfig:
            tasks:
              persona: eager-edgar
              sessionTTL: 10m
        """
    )


def _describe_match_rule(rule: dict[str, Any]) -> str:
    rule_type = rule.get("type", "unknown")
    if rule_type == "pattern":
        pattern = rule.get("pattern", "")
        properties = rule.get("properties") or ["name"]
        mode = rule.get("mode", "substring")
        props = ", ".join(properties)
        if pattern == ".+" and mode == "substring":
            return f"matches every resource (pattern `.+` on {props})"
        return f"property `{props}` must match regex `{pattern}` ({mode} mode)"
    if rule_type == "exists":
        path = rule.get("path", "")
        return f"requires JSON path `{path}` to exist on the resource"
    if rule_type == "and":
        parts = [_describe_match_rule(child) for child in rule.get("matches") or []]
        return " AND ".join(parts) if parts else "always true (empty AND)"
    if rule_type == "or":
        parts = [_describe_match_rule(child) for child in rule.get("matches") or []]
        return " OR ".join(parts) if parts else "never matches (empty OR)"
    if rule_type == "not":
        inner = rule.get("match") or {}
        return f"NOT ({_describe_match_rule(inner)})"
    return f"custom predicate type `{rule_type}`"


def _build_skill_template(
    inp: CodecollectionRenderInput, base_name: str, script_extension: str
) -> str:
    match_rules = inp.match_rules if inp.match_rules is not None else _default_match_rules()
    match_descriptions = [_describe_match_rule(rule) for rule in match_rules]
    match_bullets = (
        "\n".join(f"- {desc}" for desc in match_descriptions) or "- matches all resources"
    )
    env_vars = inp.env_vars or {}
    secret_vars = inp.secret_vars or {}
    runtime_vars = inp.runtime_vars or []
    owners = inp.owners or ["{{workspace.owner_email}}"]

    env_table = "| Name | Value | Notes |\n|---|---|---|\n"
    if env_vars:
        for name, value in env_vars.items():
            env_table += f"| `{name}` | `{value}` | static |\n"
    else:
        env_table += "| _(none)_ | | |\n"

    secret_table = "| Name | Required |\n|---|---|\n"
    if secret_vars:
        for name in secret_vars:
            secret_table += f"| `{name}` | yes |\n"
    else:
        secret_table += "| _(none)_ | no |\n"

    runtime_table = "| Name | Default | Description |\n|---|---|---|\n"
    if runtime_vars:
        for rv in runtime_vars:
            runtime_table += (
                f"| `{rv.get('name', '')}` | `{rv.get('default', '')}` | "
                f"{rv.get('description', '')} |\n"
            )
    else:
        runtime_table += "| _(none)_ | | |\n"

    lang = "python" if inp.interpreter == "python" else "bash"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    provenance_lines = [
        f"- Generated by `runwhen-platform-mcp` `render_codecollection_skill` v{inp.mcp_version}",
    ]
    if inp.source_workspace:
        provenance_lines.append(f"- Source workspace: `{inp.source_workspace}`")
    provenance_lines.append(f"- Generated at: `{generated_at}`")
    if inp.source_slx_name:
        provenance_lines.append(f"- Original inline SLX: `{inp.source_slx_name}`")

    resource_types = ", ".join(f"`{rt}`" for rt in inp.resource_types)
    qualifiers = ", ".join(f"`{q}`" for q in inp.slx_qualifiers)

    return textwrap.dedent(
        f"""\
        # {inp.alias}

        {inp.statement}

        **Bundle:** `{inp.bundle_name}` · **Task title:** `{inp.task_title}` ·
        **Runtime:** tool-builder ({lang})

        > **Review artifact:** See `raw_script.{script_extension}` in this directory
        > for the decoded script content that reviewers and automated systems should
        > inspect. Do **not** parse the base64-encoded `GEN_CMD` in the TaskSet template
        > for review purposes — use this SKILL_TEMPLATE.md and the accompanying
        > `raw_script.{script_extension}` file instead.

        ## When this SLX gets created

        - **Platform:** `{inp.platform}` — workspace-builder matches RunWhen platform resources
        - **Resource types:** {resource_types}
        - **Match rules (plain English):**
        {match_bullets}
        - **Expected cardinality:** one SLX per matched resource, named `<qualifiers>-{base_name}`
        - **Qualifiers:** {qualifiers}

        ## What this SLX does

        The task runs a {lang} script via the shared `tool-builder` codebundle in
        `rw-generic-codecollection`. The workspace-builder renders the TaskSet template with
        a base64-encoded `GEN_CMD` at deploy time; the decoded script is available in
        `raw_script.{script_extension}` in this directory for review.

        ### Environment variables

        {env_table}

        ### Secrets (names only — values live in RunWhen workspace secrets)

        {secret_table}

        ### Runtime variables (user-supplied at run time)

        {runtime_table}

        ## Operational metadata

        - **Timeout:** {inp.timeout_seconds}s
        - **Access:** `{inp.access}` · **Data:** `{inp.data}`
        - **Owners:** {", ".join(owners)}

        ## Provenance

        {chr(10).join(provenance_lines)}

        ## Deploying this codecollection

        Add to your runwhen-local runner `workspaceInfo.yaml`:

        ```yaml
        codeCollections:
          - repoURL: https://<host>/<org>/<your-private-codecollection>.git
            ref: main
        ```

        Then reconcile (Flux example):

        ```bash
        flux reconcile hr runwhen-local -n <runner-namespace> --with-source
        ```

        > **Requires runwhen-local with `platform: runwhen` support** (RW-1355). Until that
        > release lands, generation rules using `platform: runwhen` will not render SLXs.
        """
    )


def _build_bundle_readme(inp: CodecollectionRenderInput) -> str:
    lang = "py" if inp.interpreter == "python" else "sh"
    return textwrap.dedent(
        f"""\
        # {inp.bundle_name}

        Custom Discovery CodeCollection bundle generated from MCP tool-builder output.

        - **Alias:** {inp.alias}
        - **Platform:** `{inp.platform}`
        - **Resource types:** {", ".join(inp.resource_types)}

        ## Layout

        ```
        .runwhen/
          SKILL_TEMPLATE.md        # Human-readable review (decoded script + match rules)
          raw_script.{lang}        # Decoded script for reviewers and automated inspection
          generation-rules/
            {inp.bundle_name}.yaml
          templates/
            {inp.bundle_name}-slx.yaml
            {inp.bundle_name}-taskset.yaml
        ```

        See `.runwhen/SKILL_TEMPLATE.md` and `.runwhen/raw_script.{lang}` for the review
        artifacts intended for PR reviewers and automated systems.

        ## References

        - [Custom Discovery CodeCollection guide](https://docs.runwhen.com/guides/custom-discovery-codecollection/)
        - [Tool Builder Runtime guide](https://docs.runwhen.com/guides/tool-builder-runtime/)
        """
    )


def render_codecollection_files(inp: CodecollectionRenderInput) -> dict[str, str]:
    """Return relative path → file content for a complete codebundle directory."""
    base_name = _resolve_base_name(inp.bundle_name, inp.base_name)
    script_b64 = base64.b64encode(inp.script.encode("utf-8")).decode("ascii")
    script_extension = "py" if inp.interpreter == "python" else "sh"

    prefix = f"codebundles/{inp.bundle_name}"
    files: dict[str, str] = {
        f"{prefix}/README.md": _build_bundle_readme(inp),
        f"{prefix}/.runwhen/SKILL_TEMPLATE.md": _build_skill_template(
            inp, base_name, script_extension
        ),
        f"{prefix}/.runwhen/raw_script.{script_extension}": inp.script,
        f"{prefix}/.runwhen/generation-rules/{inp.bundle_name}.yaml": _build_generation_rule_yaml(
            inp, base_name
        ),
        f"{prefix}/.runwhen/templates/{inp.bundle_name}-slx.yaml": _build_slx_template(inp),
        f"{prefix}/.runwhen/templates/{inp.bundle_name}-taskset.yaml": _build_taskset_template(
            inp, script_b64
        ),
    }

    if inp.include_sli:
        if not inp.sli_script:
            raise ValueError(
                "include_sli=True requires an explicit sli_script. "
                "The SLI contract (returns float 0-1) is fundamentally different from "
                "the task contract (returns List[Dict] of issues) — they cannot share "
                "the same script body."
            )
        sli_b64 = base64.b64encode(inp.sli_script.encode("utf-8")).decode("ascii")
        files[f"{prefix}/.runwhen/templates/{inp.bundle_name}-sli.yaml"] = _build_sli_template(
            inp, sli_b64
        )

    return files


def write_codecollection_files(files: dict[str, str], output_dir: str) -> list[str]:
    """Write rendered files under *output_dir*; return absolute paths written."""
    import os

    written: list[str] = []
    for rel_path, content in files.items():
        abs_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        written.append(abs_path)
    return written
