"""Exemple instrumenté isolé ; Iris réduit le coût des tests fonctionnels."""

from pathlib import Path
import shutil

import pytest
import yaml


@pytest.fixture
def monitored_config(tmp_path):
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "config"
    shutil.copytree(root / "examples/benchmark", destination)
    dataset = yaml.safe_load((destination / "dataset.yaml").read_text())
    dataset["source"] = {"type": "builtin", "name": "iris"}
    (destination / "dataset.yaml").write_text(yaml.safe_dump(dataset))
    monitoring_path = destination / "monitoring.yaml"
    monitoring = yaml.safe_load(monitoring_path.read_text())
    # Conserver le scénario historique : monitoring local sans publication externe.
    for name in ("prometheus", "logging", "directory", "enabled"):
        monitoring.pop(name, None)
    monitoring_path.write_text(yaml.safe_dump(monitoring))
    return destination
