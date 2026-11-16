
import json
import shutil
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from signalflow.config import loader


# Lire le schema une seule fois
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/schemas/project.schema.json"
)

SCHEMA = json.loads(
    SCHEMA_PATH.read_text(encoding="utf-8")
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Isoler les tests des vrais fichiers."""
    destination = tmp_path / "config"
    shutil.copytree(loader.CONFIG, destination)
    monkeypatch.setattr(loader, "CONFIG", destination)
    return destination


@pytest.fixture
def config_data(config_dir):
    """Charger la configuration actuelle."""
    path = config_dir / "project.yaml"
    return yaml.safe_load(
        path.read_text(encoding="utf-8")
    )


def save_config(config_dir, data):
    """Sauvegarder une configuration de test."""
    path = config_dir / "project.yaml"
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8"
    )


# ==========================================
# TESTS DES CONFIGURATIONS VALIDES
# ==========================================

def test_valid_config(config_data):
    """Le YAML actuel doit etre valide."""
    result = loader.load_config()
    assert result == config_data


@pytest.mark.parametrize(
    "name",
    [
        "SignalFlow",
        "AminML",
        "CustomProject",
        "Projet Machine Learning"
    ]
)

def test_custom_project_name(
    config_dir, config_data, name
):
    """Le nom du projet doit etre personnalisable."""
    config_data["project"]["name"] = name
    save_config(config_dir, config_data)
    result = loader.load_config()
    assert result["project"]["name"] == name


# ==========================================
# TESTS DYNAMIQUES DU SCHEMA
# ==========================================

@pytest.mark.parametrize(
    "field",
    SCHEMA["required"]
)
def test_missing_root_field(
    config_dir, config_data, field
):
    """Chaque champ obligatoire doit etre verifie."""
    config_data.pop(field)
    save_config(config_dir, config_data)
    with pytest.raises(ValidationError):
        loader.load_config()


@pytest.mark.parametrize(
    "field",
    SCHEMA["properties"]["project"]["required"]
)
def test_missing_project_field(
    config_dir, config_data, field
):
    """Tester tous les champs project obligatoires."""
    config_data["project"].pop(field)
    save_config(config_dir, config_data)
    with pytest.raises(ValidationError):
        loader.load_config()


# ==========================================
# TESTS DES VALEURS INVALIDES
# ==========================================

@pytest.mark.parametrize(
    "field, value",
    [
        ("name", 123),
        ("version", False),
        ("environment", "unknown")
    ]
)

def test_invalid_project_values(
    config_dir, config_data, field, value
):
    """Verifier le rejet des valeurs invalides."""

    config_data["project"][field] = value

    save_config(config_dir, config_data)

    with pytest.raises(ValidationError):
        loader.load_config()


def test_unknown_field(config_dir, config_data):
    """Refuser les champs non declares."""
    config_data["project"]["unknown_field"] = True
    save_config(config_dir, config_data)
    with pytest.raises(ValidationError):
        loader.load_config()


def test_missing_config_file(config_dir):
    """Refuser un fichier absent."""
    (config_dir / "project.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        loader.load_config()


def test_invalid_yaml(config_dir):
    """Refuser un YAML mal forme."""
    path = config_dir / "project.yaml"
    path.write_text("project: [", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        loader.load_config()
