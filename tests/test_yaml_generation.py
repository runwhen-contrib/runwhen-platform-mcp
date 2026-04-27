"""Tests for YAML generation functions.

Verifies that _build_slx_yaml, _build_runbook_yaml, _build_sli_yaml, and
_build_cron_sli_yaml produce valid YAML with the correct structure and keys.
"""

import base64
import json

import yaml

from runwhen_platform_mcp.server import (
    _build_cron_sli_yaml,
    _build_registry_runbook_yaml,
    _build_registry_sli_yaml,
    _build_runbook_yaml,
    _build_sli_yaml,
    _build_slx_yaml,
    _enforce_custom_resource_path,
)

WS = "test-ws"
SLX = "my-check"
FULL_NAME = f"{WS}--{SLX}"


class TestBuildSlxYaml:
    """Tests for _build_slx_yaml."""

    def _parse(self, **kwargs):
        raw = _build_slx_yaml(
            workspace=WS,
            slx_name=SLX,
            alias=kwargs.get("alias", "My Check"),
            statement=kwargs.get("statement", "Things should be healthy"),
            owners=kwargs.get("owners", ["test@example.com"]),
            **{k: v for k, v in kwargs.items() if k not in ("alias", "statement", "owners")},
        )
        doc = yaml.safe_load(raw)
        assert doc is not None, "YAML output should be parseable"
        return doc

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["apiVersion"] == "runwhen.com/v1"
        assert doc["kind"] == "ServiceLevelX"
        assert doc["metadata"]["name"] == FULL_NAME
        assert doc["metadata"]["labels"]["workspace"] == WS
        assert doc["metadata"]["labels"]["slx"] == FULL_NAME
        assert doc["metadata"]["annotations"]["internal.runwhen.com/manually-created"] == "true"

    def test_spec_fields(self) -> None:
        doc = self._parse(alias="Pod Health", statement="Pods should run")
        spec = doc["spec"]
        assert spec["alias"] == "Pod Health"
        assert spec["statement"] == "Pods should run"
        assert spec["owners"] == ["test@example.com"]
        assert "imageURL" in spec
        assert isinstance(spec["tags"], list)

    def test_default_tags(self) -> None:
        doc = self._parse()
        tags = {t["name"]: t["value"] for t in doc["spec"]["tags"]}
        assert tags["access"] == "read-write"
        assert tags["data"] == "logs-bulk"

    def test_custom_tags_preserved(self) -> None:
        custom = [
            {"name": "platform", "value": "github"},
            {"name": "repo", "value": "a"},
            {"name": "repo", "value": "b"},
        ]
        doc = self._parse(tags=custom)
        tag_list = doc["spec"]["tags"]
        repo_tags = [t for t in tag_list if t["name"] == "repo"]
        assert len(repo_tags) == 2
        platform_tags = [t for t in tag_list if t["name"] == "platform"]
        assert len(platform_tags) == 1

    def test_additional_context(self) -> None:
        doc = self._parse(additional_context={"resourcePath": "github"})
        assert doc["spec"]["additionalContext"]["resourcePath"] == "github"

    def test_additional_context_with_hierarchy(self) -> None:
        doc = self._parse(
            additional_context={
                "resourcePath": "runwhen/papi",
                "hierarchy": ["resource_type", "resource_name"],
            },
        )
        ac = doc["spec"]["additionalContext"]
        assert ac["resourcePath"] == "runwhen/papi"
        assert ac["hierarchy"] == ["resource_type", "resource_name"]

    def test_additional_context_hierarchy_only(self) -> None:
        doc = self._parse(
            additional_context={"hierarchy": ["resource_type", "resource_name"]},
        )
        ac = doc["spec"]["additionalContext"]
        assert ac["hierarchy"] == ["resource_type", "resource_name"]
        assert "resourcePath" not in ac

    def test_no_additional_context_by_default(self) -> None:
        doc = self._parse()
        assert "additionalContext" not in doc["spec"]

    def test_custom_access_and_data(self) -> None:
        doc = self._parse(access="read-only", data="config")
        tags = {t["name"]: t["value"] for t in doc["spec"]["tags"]}
        assert tags["access"] == "read-only"
        assert tags["data"] == "config"


class TestBuildRunbookYaml:
    """Tests for _build_runbook_yaml."""

    SCRIPT_B64 = base64.b64encode(b"echo hello").decode()

    def _parse(self, **kwargs):
        raw = _build_runbook_yaml(
            workspace=WS,
            slx_name=SLX,
            script_b64=kwargs.get("script_b64", self.SCRIPT_B64),
            interpreter=kwargs.get("interpreter", "bash"),
            task_title=kwargs.get("task_title", "Check Health"),
            location=kwargs.get("location", "loc-01"),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ("script_b64", "interpreter", "task_title", "location")
            },
        )
        doc = yaml.safe_load(raw)
        assert doc is not None
        return doc

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["apiVersion"] == "runwhen.com/v1"
        assert doc["kind"] == "Runbook"
        assert doc["metadata"]["name"] == FULL_NAME
        assert doc["metadata"]["labels"]["workspace"] == WS
        assert doc["metadata"]["labels"]["slx"] == FULL_NAME
        assert doc["metadata"]["annotations"]["internal.runwhen.com/manually-created"] == "true"

    def test_config_provided_contains_required_keys(self) -> None:
        doc = self._parse()
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["TASK_TITLE"] == "Check Health"
        assert config["GEN_CMD"] == self.SCRIPT_B64
        assert config["INTERPRETER"] == "bash"
        assert "CONFIG_ENV_MAP" in config
        assert "SECRET_ENV_MAP" in config

    def test_env_vars_added_to_config(self) -> None:
        doc = self._parse(env_vars={"NAMESPACE": "default", "LIMIT": "10"})
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["NAMESPACE"] == "default"
        assert config["LIMIT"] == "10"
        env_map = json.loads(config["CONFIG_ENV_MAP"])
        assert env_map["NAMESPACE"] == "default"

    def test_secret_vars_added(self) -> None:
        doc = self._parse(secret_vars={"kubeconfig": "kubeconfig"})
        secrets = doc["spec"]["secretsProvided"]
        assert len(secrets) == 1
        assert secrets[0]["name"] == "kubeconfig"
        assert secrets[0]["workspaceKey"] == "kubeconfig"

    def test_no_secrets_when_empty(self) -> None:
        doc = self._parse()
        assert "secretsProvided" not in doc["spec"]

    def test_has_location_singular(self) -> None:
        doc = self._parse()
        assert doc["spec"]["location"] == "loc-01"
        assert "locations" not in doc["spec"]

    def test_no_additional_context_on_runbook(self) -> None:
        doc = self._parse()
        assert "additionalContext" not in doc["spec"]

    def test_code_bundle_present(self) -> None:
        doc = self._parse()
        cb = doc["spec"]["codeBundle"]
        assert "repoUrl" in cb
        assert "pathToRobot" in cb

    def test_code_bundle_default_ref(self) -> None:
        doc = self._parse()
        assert doc["spec"]["codeBundle"]["ref"] == "main"

    def test_code_bundle_custom_ref(self) -> None:
        doc = self._parse(codebundle_ref="v2.0")
        assert doc["spec"]["codeBundle"]["ref"] == "v2.0"

    def test_no_script_vars_by_default(self) -> None:
        """scriptVarsProvided should not appear when script_vars is omitted."""
        doc = self._parse()
        assert "scriptVarsProvided" not in doc["spec"]

    def test_script_vars_added_to_spec(self) -> None:
        """scriptVarsProvided is written to the spec when script_vars are provided."""
        doc = self._parse(
            script_vars=[
                {
                    "name": "LOG_QUERY",
                    "description": "Log filter",
                    "default": "error",
                    "validation": {"type": "regex", "pattern": "^.+$"},
                }
            ]
        )
        svp = doc["spec"].get("scriptVarsProvided")
        assert svp is not None
        assert len(svp) == 1
        assert svp[0]["name"] == "LOG_QUERY"
        assert svp[0]["default"] == "error"
        assert svp[0]["description"] == "Log filter"
        assert svp[0]["validation"]["type"] == "regex"
        assert svp[0]["validation"]["pattern"] == "^.+$"

    def test_script_vars_enum_written_correctly(self) -> None:
        doc = self._parse(
            script_vars=[
                {
                    "name": "SEVERITY",
                    "description": "Severity level",
                    "default": "warning",
                    "validation": {"type": "enum", "values": ["debug", "warning", "error"]},
                }
            ]
        )
        svp = doc["spec"]["scriptVarsProvided"]
        assert svp[0]["validation"]["values"] == ["debug", "warning", "error"]

    def test_script_vars_not_in_config_provided(self) -> None:
        """Script vars must NOT appear in configProvided — only in scriptVarsProvided."""
        doc = self._parse(
            script_vars=[
                {
                    "name": "LOG_QUERY",
                    "description": "x",
                    "default": "error",
                    "validation": {"type": "regex", "pattern": "^.+$"},
                }
            ]
        )
        config_names = [c["name"] for c in doc["spec"]["configProvided"]]
        assert "LOG_QUERY" not in config_names

    def test_empty_script_vars_omits_field(self) -> None:
        doc = self._parse(script_vars=[])
        assert "scriptVarsProvided" not in doc["spec"]


class TestBuildSliYaml:
    """Tests for _build_sli_yaml."""

    SCRIPT_B64 = base64.b64encode(b"echo 0.95").decode()

    def _parse(self, **kwargs):
        raw = _build_sli_yaml(
            workspace=WS,
            slx_name=SLX,
            script_b64=kwargs.get("script_b64", self.SCRIPT_B64),
            interpreter=kwargs.get("interpreter", "bash"),
            location=kwargs.get("location", "loc-01"),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ("script_b64", "interpreter", "location")
            },
        )
        doc = yaml.safe_load(raw)
        assert doc is not None
        return doc

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["apiVersion"] == "runwhen.com/v1"
        assert doc["kind"] == "ServiceLevelIndicator"
        assert doc["metadata"]["name"] == FULL_NAME

    def test_interval_default(self) -> None:
        doc = self._parse()
        assert doc["spec"]["intervalSeconds"] == 300

    def test_interval_custom(self) -> None:
        doc = self._parse(interval_seconds=60)
        assert doc["spec"]["intervalSeconds"] == 60

    def test_alert_config_present(self) -> None:
        doc = self._parse()
        assert "alertConfig" in doc["spec"]
        assert doc["spec"]["alertConfig"]["tasks"]["persona"] == "eager-edgar"

    def test_code_bundle_present(self) -> None:
        doc = self._parse()
        cb = doc["spec"]["codeBundle"]
        assert "repoUrl" in cb

    def test_code_bundle_default_ref(self) -> None:
        doc = self._parse()
        assert doc["spec"]["codeBundle"]["ref"] == "main"

    def test_code_bundle_custom_ref(self) -> None:
        doc = self._parse(codebundle_ref="staging")
        assert doc["spec"]["codeBundle"]["ref"] == "staging"

    def test_has_locations_list_not_singular(self) -> None:
        doc = self._parse()
        assert doc["spec"]["locations"] == ["loc-01"]
        assert "location" not in doc["spec"]

    def test_env_and_secret_vars(self) -> None:
        doc = self._parse(
            env_vars={"NS": "prod"},
            secret_vars={"kubeconfig": "kubeconfig"},
        )
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["NS"] == "prod"
        assert len(doc["spec"]["secretsProvided"]) == 1


class TestBuildCronSliYaml:
    """Tests for _build_cron_sli_yaml."""

    def _parse(self, **kwargs):
        raw = _build_cron_sli_yaml(
            workspace=WS,
            slx_name=SLX,
            location=kwargs.get("location", "loc-01"),
            cron_schedule=kwargs.get("cron_schedule", "0 */2 * * *"),
            **{k: v for k, v in kwargs.items() if k not in ("location", "cron_schedule")},
        )
        doc = yaml.safe_load(raw)
        assert doc is not None
        return doc

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["kind"] == "ServiceLevelIndicator"
        assert doc["metadata"]["name"] == FULL_NAME

    def test_has_locations_list_not_singular(self) -> None:
        doc = self._parse()
        assert doc["spec"]["locations"] == ["loc-01"]
        assert "location" not in doc["spec"]

    def test_code_bundle_default_ref(self) -> None:
        doc = self._parse()
        assert doc["spec"]["codeBundle"]["ref"] == "main"

    def test_code_bundle_always_uses_main(self) -> None:
        """Cron-SLI uses rw-workspace-utils, not the auto-detected ref."""
        doc = self._parse()
        assert doc["spec"]["codeBundle"]["ref"] == "main"
        assert "workspace-utils" in doc["spec"]["codeBundle"]["repoUrl"]

    def test_cron_schedule_in_config(self) -> None:
        doc = self._parse(cron_schedule="0 8 * * 1-5")
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["CRON_SCHEDULE"] == "0 8 * * 1-5"
        assert config["DRY_RUN"] == "false"

    def test_target_slx(self) -> None:
        doc = self._parse(target_slx="other-ws--other-slx")
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["TARGET_SLX"] == "other-ws--other-slx"

    def test_no_target_slx_by_default(self) -> None:
        doc = self._parse()
        config_names = [c["name"] for c in doc["spec"]["configProvided"]]
        assert "TARGET_SLX" not in config_names

    def test_dry_run(self) -> None:
        doc = self._parse(dry_run=True)
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["DRY_RUN"] == "true"


REPO_URL = "https://github.com/runwhen-contrib/rw-cli-codecollection.git"
CB_PATH = "codebundles/k8s-namespace-healthcheck"


class TestBuildRegistryRunbookYaml:
    """Tests for _build_registry_runbook_yaml."""

    def _parse(self, **kwargs):
        defaults = dict(
            workspace=WS,
            slx_name=SLX,
            repo_url=REPO_URL,
            path_to_robot=f"{CB_PATH}/runbook.robot",
            location="loc-01",
        )
        defaults.update(kwargs)
        raw = _build_registry_runbook_yaml(**defaults)
        return yaml.safe_load(raw)

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["apiVersion"] == "runwhen.com/v1"
        assert doc["kind"] == "Runbook"
        assert doc["metadata"]["name"] == FULL_NAME

    def test_code_bundle_points_to_codebundle_repo(self) -> None:
        doc = self._parse()
        cb = doc["spec"]["codeBundle"]
        assert cb["repoUrl"] == REPO_URL
        assert cb["pathToRobot"] == f"{CB_PATH}/runbook.robot"
        assert cb["ref"] == "main"

    def test_no_gen_cmd_or_interpreter(self) -> None:
        """Registry runbooks should NOT have Tool Builder config vars."""
        doc = self._parse()
        config_names = [c["name"] for c in doc["spec"]["configProvided"]]
        assert "GEN_CMD" not in config_names
        assert "INTERPRETER" not in config_names

    def test_config_vars_present(self) -> None:
        doc = self._parse(config_vars={"NAMESPACE": "prod", "CONTEXT": "my-cluster"})
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["NAMESPACE"] == "prod"
        assert config["CONTEXT"] == "my-cluster"

    def test_secret_vars_present(self) -> None:
        doc = self._parse(secret_vars={"kubeconfig": "kubeconfig"})
        secrets = doc["spec"]["secretsProvided"]
        assert secrets[0]["name"] == "kubeconfig"
        assert secrets[0]["workspaceKey"] == "kubeconfig"

    def test_no_secrets_when_empty(self) -> None:
        doc = self._parse()
        assert "secretsProvided" not in doc["spec"]

    def test_custom_ref(self) -> None:
        doc = self._parse(ref="v2.1.0")
        assert doc["spec"]["codeBundle"]["ref"] == "v2.1.0"

    def test_has_location_singular(self) -> None:
        doc = self._parse()
        assert "location" in doc["spec"]
        assert "locations" not in doc["spec"]


class TestBuildRegistrySliYaml:
    """Tests for _build_registry_sli_yaml."""

    def _parse(self, **kwargs):
        defaults = dict(
            workspace=WS,
            slx_name=SLX,
            repo_url=REPO_URL,
            path_to_robot=f"{CB_PATH}/sli.robot",
            location="loc-01",
        )
        defaults.update(kwargs)
        raw = _build_registry_sli_yaml(**defaults)
        return yaml.safe_load(raw)

    def test_basic_structure(self) -> None:
        doc = self._parse()
        assert doc["apiVersion"] == "runwhen.com/v1"
        assert doc["kind"] == "ServiceLevelIndicator"
        assert doc["metadata"]["name"] == FULL_NAME

    def test_code_bundle_points_to_codebundle_repo(self) -> None:
        doc = self._parse()
        cb = doc["spec"]["codeBundle"]
        assert cb["repoUrl"] == REPO_URL
        assert cb["pathToRobot"] == f"{CB_PATH}/sli.robot"
        assert cb["ref"] == "main"

    def test_no_gen_cmd_or_interpreter(self) -> None:
        doc = self._parse()
        config_names = [c["name"] for c in doc["spec"]["configProvided"]]
        assert "GEN_CMD" not in config_names
        assert "INTERPRETER" not in config_names

    def test_has_locations_list(self) -> None:
        doc = self._parse()
        assert "locations" in doc["spec"]
        assert isinstance(doc["spec"]["locations"], list)

    def test_interval_default(self) -> None:
        doc = self._parse()
        assert doc["spec"]["intervalSeconds"] == 300

    def test_interval_custom(self) -> None:
        doc = self._parse(interval_seconds=180)
        assert doc["spec"]["intervalSeconds"] == 180

    def test_alert_config_present(self) -> None:
        doc = self._parse()
        assert "alertConfig" in doc["spec"]

    def test_description(self) -> None:
        doc = self._parse(description="Measures namespace health")
        assert doc["spec"]["description"] == "Measures namespace health"

    def test_config_and_secret_vars(self) -> None:
        doc = self._parse(
            config_vars={"NAMESPACE": "prod"},
            secret_vars={"kubeconfig": "kubeconfig"},
        )
        config = {c["name"]: c["value"] for c in doc["spec"]["configProvided"]}
        assert config["NAMESPACE"] == "prod"
        secrets = doc["spec"]["secretsProvided"]
        assert secrets[0]["name"] == "kubeconfig"


class TestEnforceCustomResourcePath:
    """Tests for _enforce_custom_resource_path."""

    def test_none_returns_none(self) -> None:
        assert _enforce_custom_resource_path(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _enforce_custom_resource_path("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _enforce_custom_resource_path("   ") is None

    def test_already_prefixed_unchanged(self) -> None:
        assert _enforce_custom_resource_path("custom/my-app") == "custom/my-app"

    def test_already_prefixed_case_normalised(self) -> None:
        assert _enforce_custom_resource_path("Custom/my-app") == "custom/my-app"

    def test_uppercase_prefix_normalised(self) -> None:
        assert _enforce_custom_resource_path("CUSTOM/my-app") == "custom/my-app"

    def test_prepends_custom_prefix(self) -> None:
        assert (
            _enforce_custom_resource_path("kubernetes/cluster-01/ns")
            == "custom/kubernetes/cluster-01/ns"
        )

    def test_prepends_to_plain_path(self) -> None:
        assert _enforce_custom_resource_path("my-app") == "custom/my-app"

    def test_strips_trailing_slash(self) -> None:
        assert _enforce_custom_resource_path("custom/my-app/") == "custom/my-app"

    def test_strips_whitespace(self) -> None:
        assert _enforce_custom_resource_path("  custom/my-app  ") == "custom/my-app"

    def test_non_custom_platform_gets_prefix(self) -> None:
        assert (
            _enforce_custom_resource_path("aws/us-east-1/lambda/fn")
            == "custom/aws/us-east-1/lambda/fn"
        )

    def test_deeply_nested_custom_path(self) -> None:
        assert (
            _enforce_custom_resource_path("custom/k8s/cluster/ns/app")
            == "custom/k8s/cluster/ns/app"
        )

    def test_bare_custom_slash_returns_none(self) -> None:
        assert _enforce_custom_resource_path("custom/") is None

    def test_bare_custom_multiple_slashes_returns_none(self) -> None:
        assert _enforce_custom_resource_path("custom///") is None

    def test_only_slashes_returns_none(self) -> None:
        assert _enforce_custom_resource_path("///") is None

    def test_non_prefixed_trailing_slash_stripped(self) -> None:
        assert _enforce_custom_resource_path("kubernetes/ns/") == "custom/kubernetes/ns"
