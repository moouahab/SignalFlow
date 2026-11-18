"""Fixtures Ollama : configuration locale activée, sans service externe."""

import shutil
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def llm_config(tmp_path):
    """Copier config/ et activer le fournisseur llm."""
    destination = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", destination)
    tools_path = destination / "tools.yaml"
    tools = yaml.safe_load(tools_path.read_text(encoding="utf-8"))
    tools["tools"]["llm"]["enabled"] = True
    tools_path.write_text(yaml.safe_dump(tools), encoding="utf-8")
    # Les tests sont hors ligne : remplacer l'adresse detectee sur la machine
    # par une valeur deterministe, sans modifier la configuration du depot.
    llm_path = destination / "llm.yaml"
    llm = yaml.safe_load(llm_path.read_text(encoding="utf-8"))
    llm["ollama"]["base_url"] = "http://127.0.0.1:11434"
    llm_path.write_text(yaml.safe_dump(llm), encoding="utf-8")
    return destination


@pytest.fixture
def llm_config_patched(llm_config):
    """Renvoyer (dossier, chargeur de document) pour muter llm.yaml."""
    path = llm_config / "llm.yaml"

    def load():
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def save(document):
        path.write_text(yaml.safe_dump(document), encoding="utf-8")

    return llm_config, load, save
