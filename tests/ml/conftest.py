"""Configurations d'exemple isolées pour chaque test."""

import shutil
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def ml_config(tmp_path):
    destination = tmp_path / "configuration"
    shutil.copytree(Path(__file__).resolve().parents[2] / "examples/classification", destination)
    project_path = destination / "project.yaml"
    project = yaml.safe_load(project_path.read_text())
    project["config"].pop("monitoring", None)
    project_path.write_text(yaml.safe_dump(project))
    tools_path = destination / "tools.yaml"
    tools = yaml.safe_load(tools_path.read_text())
    tools["tools"].pop("metrics", None)
    tools_path.write_text(yaml.safe_dump(tools))
    return destination


@pytest.fixture
def documents(ml_config):
    return {
        name: yaml.safe_load((ml_config / f"{name}.yaml").read_text())
        for name in ("training", "dataset", "mlops")
    }
