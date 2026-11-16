"""Configurations autonomes avec publication confinée au dossier temporaire du test."""
from pathlib import Path
import shutil
import pytest


@pytest.fixture
def telemetry_config(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / 'configuration'
    shutil.copytree(root / 'examples/classification', target)
    monkeypatch.setenv('SIGNALFLOW_OBSERVABILITY_DIR', str(tmp_path / 'telemetry'))
    return target
