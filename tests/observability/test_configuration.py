"""Contrats observabilité/benchmark sans imports ML optionnels."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from signalflow.config.loader.errors import ConfigValidationError, ConfigPathNotFoundError
from signalflow.ml.config import load_run_configuration
from signalflow.ml.errors import PipelineError
from signalflow.ml.registry import create_registry
from signalflow.benchmarking.runner import _load_spec, BenchmarkError


def modify(base, file, field, value):
    path = base / file
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data))


def load(base):
    return load_run_configuration(base, registry=create_registry())


@pytest.mark.parametrize("field,value", [
    ("interval_seconds", 0), ("interval_seconds", 0.001),
    ("interval_seconds", "0.1"), ("max_samples", 1), ("unknown", True),
])
def test_monitoring_contract(monitored_config, field, value):
    modify(monitored_config, "monitoring.yaml", field, value)
    with pytest.raises(ConfigValidationError):
        load(monitored_config)


def test_nan_interval(monitored_config):
    modify(monitored_config, "monitoring.yaml", "interval_seconds", float('nan'))
    with pytest.raises(PipelineError):
        load(monitored_config)


def test_missing_active_monitoring_file(monitored_config):
    (monitored_config / "monitoring.yaml").unlink()
    with pytest.raises(ConfigPathNotFoundError):
        load(monitored_config)


def test_disabled_monitoring_does_not_read_settings(monitored_config):
    path = monitored_config / "tools.yaml"
    data = yaml.safe_load(path.read_text())
    data["tools"]["metrics"]["enabled"] = False
    path.write_text(yaml.safe_dump(data))
    (monitored_config / "monitoring.yaml").unlink()
    assert load(monitored_config).monitoring is None


@pytest.mark.parametrize("field,value", [("repetitions", 1), ("warmup_repetitions", -1), ("timeout_seconds", 0)])
def test_invalid_benchmark_structure(monitored_config, field, value):
    modify(monitored_config, "benchmark.yaml", field, value)
    with pytest.raises(ConfigValidationError):
        _load_spec(monitored_config, "./benchmark.yaml")


def test_benchmark_seed_mismatch(monitored_config):
    modify(monitored_config, "training-regularized.yaml", "seed", 7)
    with pytest.raises(BenchmarkError):
        _load_spec(monitored_config, "./benchmark.yaml")


def test_benchmark_duplicate_names(monitored_config):
    modify(monitored_config, "benchmark.yaml", "configurations", [
        {"name": "same", "training": "./training.yaml"},
        {"name": "same", "training": "./training-regularized.yaml"},
    ])
    with pytest.raises(BenchmarkError):
        _load_spec(monitored_config, "./benchmark.yaml")


def test_local_collector_dependency_is_lazy(monitored_config, tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'psutil', 'sklearn', 'mlflow', 'numpy'}:
            raise ImportError('blocked optional library')
sys.meta_path.insert(0, Block())
from signalflow.ml.config import load_run_configuration
from signalflow.ml.registry import create_registry
from signalflow.tools import ToolDeclaration, ToolUnavailableError
from signalflow.benchmarking import run_benchmark
registry = create_registry()
load_run_configuration(sys.argv[1], registry=registry)
assert registry.initialize([ToolDeclaration('metrics', 'local', False)]) == {}
try:
    registry.initialize([ToolDeclaration('metrics', 'local', True)])
except ToolUnavailableError:
    pass
else:
    raise AssertionError('missing psutil not reported')
'''
    subprocess.run([sys.executable, "-c", code, str(monitored_config)], cwd=tmp_path,
                   env=dict(os.environ, PYTHONPATH=str(root / "src")), check=True, timeout=30)


@pytest.mark.parametrize("dimensions", [
    {"n_features": 4, "n_informative": 4, "n_redundant": 2},
    {"n_features": 4, "n_informative": 1, "n_redundant": 0},
])
def test_synthetic_semantic_validation(monitored_config, dimensions):
    path = monitored_config / "dataset.yaml"
    data = yaml.safe_load(path.read_text())
    data["source"] = {"type": "synthetic", "n_samples": 100, "n_classes": 3,
                      "random_state": 123, **dimensions}
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(PipelineError):
        load(monitored_config)
