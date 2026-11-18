"""Contrat complet des outils, sans SDK ni service externe."""

import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from signalflow.config.loader import load_config, load_configuration
from signalflow.config.loader.errors import (
    ConfigDuplicateKeyError,
    ConfigPathNotFoundError,
    ConfigPathSymlinkError,
    ConfigPathTraversalError,
    ConfigSyntaxError,
    ConfigTooLargeError,
    ConfigValidationError,
)
from signalflow.config.loader.settings import Limits
from signalflow.ml.registry import create_registry
from signalflow.tools import (
    ToolDeclaration,
    ToolInitializationError,
    ToolRegistry,
    ToolUnavailableError,
    ToolUnknownError,
)


@pytest.fixture
def config_dir(tmp_path):
    destination = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", destination)
    return destination


def write_tools(base, tools):
    (base / "tools.yaml").write_text(
        yaml.safe_dump({"schema_version": "0.0.1", "tools": tools}), encoding="utf-8"
    )


def update_reference(base, **changes):
    path = base / "project.yaml"
    project = yaml.safe_load(path.read_text())
    project["config"]["tools"].update(changes)
    path.write_text(yaml.safe_dump(project), encoding="utf-8")


def test_valid_configuration_and_legacy_api(config_dir):
    configuration = load_configuration(config_dir)
    assert configuration.project == load_config(config_dir)
    assert len(configuration.tools) == 8
    enabled = [tool.category for tool in configuration.tools if tool.enabled]
    assert enabled == ["llm"]
    assert set(create_registry().initialize(configuration.tools)) == {"llm"}


@pytest.mark.parametrize("document", [
    {},
    {"schema_version": "2", "tools": {}},
    {"schema_version": "0.0.1", "tools": {}, "secret": "not-allowed"},
    {"schema_version": "0.0.1", "tools": []},
])
def test_invalid_document(config_dir, document):
    (config_dir / "tools.yaml").write_text(yaml.safe_dump(document))
    with pytest.raises(ConfigValidationError):
        load_configuration(config_dir)


@pytest.mark.parametrize("tools", [
    {"unknown_category": {"provider": "pandas", "enabled": False}},
    {"data": {"provider": "pandas"}},
    {"data": {"enabled": True}},
    {"data": {"provider": 1, "enabled": False}},
    {"data": {"provider": "pandas", "enabled": "false"}},
    {"data": {"provider": "pandas", "enabled": False, "config": 42}},
    {"data": {"provider": "pandas", "enabled": False, "token": "secret"}},
    {"training": {"provider": "scikit-learn", "enabled": False, "epochs": 10}},
    {"data": {"provider": "pandas", "enabled": False, "config": "/etc/test.yaml"}},
])
def test_invalid_selection(config_dir, tools):
    write_tools(config_dir, tools)
    with pytest.raises(ConfigValidationError):
        load_config(config_dir)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("provider", ["unknown", "mlflow", "os.system"])
def test_unknown_or_wrong_category_provider(config_dir, provider, enabled):
    write_tools(config_dir, {"data": {"provider": provider, "enabled": enabled}})
    with pytest.raises(ToolUnknownError) as error:
        load_configuration(config_dir)
    assert error.value.config_path == "$.tools.data.provider"
    assert error.value.file == config_dir / "tools.yaml"


def test_required_file_missing(config_dir):
    (config_dir / "tools.yaml").unlink()
    with pytest.raises(ConfigPathNotFoundError):
        load_config(config_dir)


def test_optional_file_missing(config_dir):
    update_reference(config_dir, required=False)
    (config_dir / "tools.yaml").unlink()
    assert load_configuration(config_dir).tools == ()


def test_optional_invalid_file_is_not_ignored(config_dir):
    update_reference(config_dir, required=False)
    write_tools(config_dir, {"data": {}})
    with pytest.raises(ConfigValidationError):
        load_configuration(config_dir)


def test_disabled_project_section_is_not_read(config_dir):
    update_reference(config_dir, enabled=False)
    (config_dir / "tools.yaml").write_text("not valid: [")
    assert load_configuration(config_dir).tools == ()


def test_schema_missing(config_dir):
    (config_dir / "schemas/tools.schema.json").unlink()
    with pytest.raises(ConfigPathNotFoundError):
        load_configuration(config_dir)


@pytest.mark.parametrize("field,value", [
    ("path", "./../outside.yaml"),
    ("schema", "./schemas/../../outside.schema.json"),
])
def test_project_reference_traversal(config_dir, field, value):
    update_reference(config_dir, **{field: value})
    with pytest.raises(ConfigPathTraversalError):
        load_configuration(config_dir)


def test_optional_traversal_is_not_ignored(config_dir):
    update_reference(config_dir, required=False, path="./../outside.yaml")
    with pytest.raises(ConfigPathTraversalError):
        load_configuration(config_dir)


@pytest.mark.parametrize("enabled", [True, False])
def test_specialized_reference_traversal(config_dir, enabled):
    write_tools(config_dir, {"data": {
        "provider": "pandas", "enabled": enabled, "config": "./../outside.yaml",
    }})
    with pytest.raises(ConfigPathTraversalError):
        load_configuration(config_dir)


@pytest.mark.parametrize("target", ["tools.yaml", "schemas/tools.schema.json", "special.yaml"])
def test_symlink_escape(config_dir, tmp_path, target):
    outside = tmp_path / "outside.yaml"
    outside.write_text("{}")
    path = config_dir / target
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    if target == "special.yaml":
        write_tools(config_dir, {"data": {
            "provider": "pandas", "enabled": False, "config": "./special.yaml",
        }})
    with pytest.raises(ConfigPathSymlinkError):
        load_configuration(config_dir)


def test_specialized_file_missing(config_dir):
    write_tools(config_dir, {"training": {
        "provider": "scikit-learn", "enabled": False, "config": "./training.yaml",
    }})
    with pytest.raises(ConfigPathNotFoundError):
        load_configuration(config_dir)


def test_specialized_reference_resolved_from_project(config_dir, tmp_path, monkeypatch):
    (config_dir / "training.yaml").write_text("epochs: 3")
    (config_dir / "nested").mkdir()
    update_reference(config_dir, path="./nested/tools.yaml")
    write_tools(config_dir, {"training": {
        "provider": "scikit-learn", "enabled": False, "config": "./training.yaml",
    }})
    (config_dir / "tools.yaml").rename(config_dir / "nested/tools.yaml")
    monkeypatch.chdir(tmp_path)
    selection, = load_configuration(config_dir).tools
    assert selection.config == config_dir / "training.yaml"


def test_duplicate_yaml_key(config_dir):
    (config_dir / "tools.yaml").write_text(
        'schema_version: "0.0.1"\ntools:\n  data:\n'
        '    provider: pandas\n    enabled: false\n    enabled: true\n'
    )
    with pytest.raises(ConfigDuplicateKeyError):
        load_config(config_dir)


def test_malformed_yaml(config_dir):
    (config_dir / "tools.yaml").write_text("tools: [")
    with pytest.raises(ConfigSyntaxError):
        load_configuration(config_dir)


def test_tools_size_limit(config_dir):
    (config_dir / "tools.yaml").write_text("#" * 10000)
    with pytest.raises(ConfigTooLargeError) as error:
        load_configuration(config_dir, limits=Limits(config_bytes=5000))
    assert error.value.file == config_dir / "tools.yaml"


def test_registry_extension_and_explicit_initialization(config_dir):
    write_tools(config_dir, {"data": {"provider": "local-test", "enabled": True}})
    registry = ToolRegistry()
    instance = object()
    factory = Mock(return_value=instance)
    registry.register("data", "local-test", factory)
    configuration = load_configuration(config_dir, registry=registry)
    factory.assert_not_called()
    assert registry.initialize(configuration.tools) == {"data": instance}
    factory.assert_called_once_with(configuration.tools[0])


def test_disabled_factory_never_called(config_dir):
    write_tools(config_dir, {"data": {"provider": "pandas", "enabled": False}})
    registry = ToolRegistry()
    factory = Mock(side_effect=AssertionError("must not run"))
    registry.register("data", "pandas", factory)
    configuration = load_configuration(config_dir, registry=registry)
    assert registry.initialize(configuration.tools) == {}
    factory.assert_not_called()


def test_enabled_known_unavailable_can_be_validated_but_not_initialized(config_dir):
    write_tools(config_dir, {"experiment_tracking": {"provider": "mlflow", "enabled": True}})
    registry = ToolRegistry()
    configuration = load_configuration(config_dir, registry=registry)
    assert registry.resolve("experiment_tracking", "mlflow").factory is None
    with pytest.raises(ToolUnavailableError):
        registry.initialize(configuration.tools)


def test_preflight_before_any_initialization():
    registry = ToolRegistry()
    factory = Mock()
    registry.register("data", "pandas", factory)
    with pytest.raises(ToolUnavailableError):
        registry.initialize([
            ToolDeclaration("data", "pandas", True),
            ToolDeclaration("execution", "docker", True),
        ])
    factory.assert_not_called()


@pytest.mark.parametrize("error,expected", [
    (ImportError("token=SECRET"), ToolUnavailableError),
    (RuntimeError("token=SECRET"), ToolInitializationError),
])
def test_factory_errors_redacted(error, expected):
    import traceback

    registry = ToolRegistry()
    registry.register("data", "pandas", Mock(side_effect=error))
    with pytest.raises(expected) as captured:
        registry.initialize([ToolDeclaration("data", "pandas", True)])
    assert "SECRET" not in "".join(traceback.format_exception(captured.value))


def test_registry_isolation_and_duplicate_registration():
    registry = ToolRegistry()
    factory = Mock()
    registry.register("data", "pandas", factory)
    assert ToolRegistry().resolve("data", "pandas").factory is None
    with pytest.raises(ValueError):
        registry.register("data", "pandas", factory)
    with pytest.raises(ToolUnknownError):
        registry.register("unknown", "pandas", factory)


def test_empty_tools(config_dir):
    write_tools(config_dir, {})
    assert load_configuration(config_dir).tools == ()


def test_each_configuration_is_read_once(config_dir, monkeypatch):
    from signalflow.config.loader import files

    read = Mock(wraps=files.read_bytes)
    monkeypatch.setattr(files, "read_bytes", read)
    load_configuration(config_dir)
    paths = [call.args[0] for call in read.call_args_list]
    assert len(paths) == len(set(paths)) == 4


def test_schema_categories_match_registry(config_dir):
    from signalflow.tools.registry import KNOWN_PROVIDERS

    schema = json.loads((config_dir / "schemas/tools.schema.json").read_text())
    assert set(schema["properties"]["tools"]["properties"]) == set(KNOWN_PROVIDERS)


@pytest.mark.parametrize("reference", ["https://example.invalid/schema.json", "file:///etc/schema.json"])
def test_external_schema_references_are_not_retrieved(config_dir, monkeypatch, reference):
    from signalflow.config.loader.errors import ConfigSchemaError
    import urllib.request

    fetch = Mock(side_effect=AssertionError("no network allowed"))
    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    (config_dir / "schemas/tools.schema.json").write_text(json.dumps({"$ref": reference}))
    with pytest.raises(ConfigSchemaError):
        load_configuration(config_dir)
    fetch.assert_not_called()


def test_file_read_is_bounded(tmp_path, monkeypatch):
    from signalflow.config.loader.files import read_bytes

    path = tmp_path / "large"
    path.write_bytes(b"x" * 100)
    original_open = Path.open
    stream = Mock(wraps=original_open(path, "rb"))
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(side_effect=lambda *args: stream.close())
    monkeypatch.setattr(Path, "open", Mock(return_value=stream))
    with pytest.raises(ConfigTooLargeError):
        read_bytes(path, max_bytes=10)
    stream.read.assert_called_once_with(11)


def test_registry_rejects_unknown_disabled_provider_and_duplicate_category():
    registry = ToolRegistry()
    with pytest.raises(ToolUnknownError):
        registry.initialize([ToolDeclaration("data", "unregistered", False)])
    with pytest.raises(ToolInitializationError):
        registry.initialize([ToolDeclaration("data", "pandas", False)] * 2)
