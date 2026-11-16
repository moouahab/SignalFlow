"""Validation du pipeline sans import des dépendances ML optionnelles."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from signalflow.config.loader.errors import (
    ConfigDuplicateKeyError, ConfigPathNotFoundError, ConfigPathTraversalError,
    ConfigValidationError,
)
from signalflow.ml.config import load_run_configuration
from signalflow.ml.errors import PipelineError
from signalflow.ml.registry import create_registry


def change(base, name, path, value):
    file = base / f"{name}.yaml"
    data = yaml.safe_load(file.read_text())
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    file.write_text(yaml.safe_dump(data))


def load(base, **kwargs):
    return load_run_configuration(base, registry=create_registry(), **kwargs)


def test_valid_and_override(ml_config):
    configuration = load(ml_config)
    assert configuration.training["model"]["parameters"]["C"] == 1.0
    other = load(ml_config, training_override="./training-regularized.yaml")
    assert other.training["model"]["parameters"]["C"] == 0.05
    assert other.training["seed"] == configuration.training["seed"]
    assert other.snapshot()["sources"]["training"].endswith("training-regularized.yaml")


@pytest.mark.parametrize("path,value", [
    (["schema_version"], "1"), (["seed"], -1), (["seed"], True),
    (["seed"], 4294967296), (["model", "type"], "os.system"),
    (["model", "parameters", "C"], 0), (["model", "parameters", "C"], "1"),
    (["model", "parameters", "max_iter"], 0),
    (["model", "parameters", "unknown"], 1), (["unknown"], "secret"),
])
def test_invalid_training(ml_config, path, value):
    change(ml_config, "training", path, value)
    with pytest.raises(ConfigValidationError):
        load(ml_config)


def test_missing_training_field(ml_config):
    (ml_config / "training.yaml").write_text('schema_version: "0.0.1"\n')
    with pytest.raises(ConfigValidationError):
        load(ml_config)


@pytest.mark.parametrize("component,path,value", [
    ("training", ["model", "parameters", "C"], float('nan')),
    ("dataset", ["split", "test"], float('nan')),
    ("dataset", ["split", "validation"], 0.8),
])
def test_semantic_numeric_checks(ml_config, component, path, value):
    change(ml_config, component, path, value)
    with pytest.raises(PipelineError):
        load(ml_config)


@pytest.mark.parametrize("component,path,value", [
    ("dataset", ["split", "stratify"], "yes"),
    ("dataset", ["source", "name"], "remote_dataset"),
    ("dataset", ["source"], {"type": "csv", "path": "/tmp/data.csv", "target": "y"}),
    ("mlops", ["tracking_uri"], "https://example.invalid"),
    ("mlops", ["experiment_name"], ""),
])
def test_dataset_tracking_structure(ml_config, component, path, value):
    change(ml_config, component, path, value)
    with pytest.raises(ConfigValidationError):
        load(ml_config)


@pytest.mark.parametrize("category", ["training", "experiment_tracking"])
def test_required_provider_disabled(ml_config, category):
    change(ml_config, "tools", ["tools", category, "enabled"], False)
    with pytest.raises(PipelineError):
        load(ml_config)


def test_unsupported_pipeline_step_not_silently_skipped(ml_config):
    change(ml_config, "project", ["pipeline", "steps"], ["train", "security_scan"])
    with pytest.raises(PipelineError):
        load(ml_config)


def test_specialized_reference_conflict(ml_config):
    change(ml_config, "tools", ["tools", "training", "config"], "./training-regularized.yaml")
    with pytest.raises(PipelineError):
        load(ml_config)


def test_required_component_disabled(ml_config):
    change(ml_config, "project", ["config", "dataset", "enabled"], False)
    with pytest.raises(PipelineError):
        load(ml_config)


def test_missing_training_file(ml_config):
    (ml_config / "training.yaml").unlink()
    with pytest.raises(ConfigPathNotFoundError):
        load(ml_config)


def test_duplicate_training_key(ml_config):
    with (ml_config / "training.yaml").open("a") as stream:
        stream.write("seed: 4\n")
    with pytest.raises(ConfigDuplicateKeyError):
        load(ml_config)


def test_csv_path_traversal(ml_config):
    change(ml_config, "dataset", ["source"], {"type": "csv", "path": "./../data.csv", "target": "y"})
    with pytest.raises(ConfigPathTraversalError):
        load(ml_config)


def test_training_override_path_traversal(ml_config):
    with pytest.raises(ConfigPathTraversalError):
        load(ml_config, training_override="../secret.yaml")


def test_configuration_files_read_once(ml_config, monkeypatch):
    from signalflow.config.loader import files
    from unittest.mock import Mock
    read = Mock(wraps=files.read_bytes)
    monkeypatch.setattr(files, "read_bytes", read)
    load(ml_config)
    paths = [call.args[0] for call in read.call_args_list]
    assert len(paths) == len(set(paths)) == 10


def test_schema_examples_match_canonical():
    root = Path(__file__).resolve().parents[2]
    for name in ("project", "tools", "training", "dataset", "mlops"):
        filename = f"{name}.schema.json"
        assert json.loads((root / "config/schemas" / filename).read_text()) == json.loads(
            (root / "examples/classification/schemas" / filename).read_text()
        )


def test_optional_imports_and_missing_dependency(ml_config, tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sklearn', 'mlflow', 'numpy'}:
            raise ImportError('optional dependency deliberately blocked')
sys.meta_path.insert(0, Block())
from signalflow.ml.registry import create_registry
from signalflow.ml.config import load_run_configuration
from signalflow.ml.runner import run_training
from signalflow.tools import ToolDeclaration, ToolUnavailableError
registry = create_registry()
configuration = load_run_configuration(sys.argv[1], registry=registry)
assert registry.initialize([ToolDeclaration('training', 'scikit-learn', False),
                            ToolDeclaration('experiment_tracking', 'mlflow', False)]) == {}
for category, name in [('training', 'scikit-learn'), ('experiment_tracking', 'mlflow')]:
    try:
        registry.initialize([ToolDeclaration(category, name, True)])
    except ToolUnavailableError:
        pass
    else:
        raise AssertionError('missing optional dependency was not reported')
assert not any(name in sys.modules for name in ('sklearn', 'numpy', 'mlflow'))
'''
    subprocess.run([sys.executable, "-c", code, str(ml_config)], cwd=tmp_path,
                   env=dict(os.environ, PYTHONPATH=str(root / "src")), check=True, timeout=30)


def test_cli_help_without_ml_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sklearn', 'mlflow', 'numpy'}:
            raise AssertionError('help must not import ML dependencies')
sys.meta_path.insert(0, Block())
from signalflow.cli import main
main(['--help'])
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=str(root / "src")),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "train" in result.stdout
