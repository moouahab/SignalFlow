"""Tests des protections d'erreur du chargeur de configuration."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from jsonschema.exceptions import ValidationError

from signalflow.config import loader
from signalflow.config.loader.errors import (
    ConfigDuplicateKeyError,
    ConfigEmptyError,
    ConfigEncodingError,
    ConfigNotFoundError,
    ConfigSchemaError,
    ConfigSyntaxError,
    ConfigTooLargeError,
    ConfigValidationError,
)
from signalflow.config.loader.settings import Limits


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Copier la configuration reelle dans un dossier isole."""
    destination = tmp_path / "config"
    shutil.copytree(loader.CONFIG, destination)
    monkeypatch.setattr(loader, "CONFIG", destination)
    return destination


def read_config(config_dir):
    return yaml.safe_load(
        (config_dir / "project.yaml").read_text(encoding="utf-8")
    )


def write_config(config_dir, data):
    (config_dir / "project.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )


# ==========================================
# COMPATIBILITE DU CONTRAT
# ==========================================

def test_valid_config_returns_dict(config_dir):
    result = loader.load_config()
    assert isinstance(result, dict)
    assert result["project"]["name"] == "SignalFlow"


def test_explicit_config_dir(config_dir):
    result = loader.load_config(config_dir=config_dir)
    assert result["schema_version"] == "0.0.1"


def test_not_found_is_backward_compatible(config_dir):
    (config_dir / "project.yaml").unlink()
    with pytest.raises(ConfigNotFoundError) as info:
        loader.load_config()
    assert isinstance(info.value, FileNotFoundError)
    assert info.value.code == "CONFIG_NOT_FOUND"
    assert "project.yaml" in str(info.value)


def test_syntax_error_is_backward_compatible(config_dir):
    (config_dir / "project.yaml").write_text("project: [", encoding="utf-8")
    with pytest.raises(ConfigSyntaxError) as info:
        loader.load_config()
    assert isinstance(info.value, yaml.YAMLError)
    assert info.value.code == "CONFIG_SYNTAX"


# ==========================================
# LECTURE ET SYNTAXE
# ==========================================

def test_empty_file(config_dir):
    (config_dir / "project.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigEmptyError) as info:
        loader.load_config()
    assert info.value.code == "CONFIG_EMPTY"


@pytest.mark.parametrize(
    "content",
    [
        "- a\n- b\n",
        "juste une chaine\n",
        "42\n",
    ],
)
def test_root_must_be_mapping(config_dir, content):
    (config_dir / "project.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigSyntaxError):
        loader.load_config()


def test_duplicate_key(config_dir):
    content = (config_dir / "project.yaml").read_text(encoding="utf-8")
    content = content.replace(
        "  name: SignalFlow",
        "  name: SignalFlow\n  name: Doublon",
    )
    (config_dir / "project.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigDuplicateKeyError) as info:
        loader.load_config()
    assert info.value.code == "CONFIG_DUPLICATE_KEY"
    assert "name" in str(info.value)
    assert "ligne" in str(info.value)
    assert "colonne" in str(info.value)


def test_multiple_documents(config_dir):
    content = (config_dir / "project.yaml").read_text(encoding="utf-8")
    (config_dir / "project.yaml").write_text(
        content + "\n---\nautre: document\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigSyntaxError) as info:
        loader.load_config()
    assert "document" in str(info.value)


def test_syntax_error_reports_position(config_dir):
    (config_dir / "project.yaml").write_text("project: [\n", encoding="utf-8")
    with pytest.raises(ConfigSyntaxError) as info:
        loader.load_config()
    assert "ligne" in str(info.value)
    assert "colonne" in str(info.value)


def test_invalid_encoding(config_dir):
    (config_dir / "project.yaml").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ConfigEncodingError) as info:
        loader.load_config()
    assert info.value.code == "CONFIG_ENCODING"


def test_too_large(config_dir):
    with pytest.raises(ConfigTooLargeError) as info:
        loader.load_config(limits=Limits(config_bytes=10))
    assert info.value.code == "CONFIG_TOO_LARGE"


def test_schema_too_large(config_dir):
    with pytest.raises(ConfigTooLargeError) as info:
        loader.load_config(limits=Limits(schema_bytes=10))
    assert "project.schema.json" in str(info.value)


# ==========================================
# SCHEMA
# ==========================================

def test_missing_schema(config_dir):
    (config_dir / "schemas/project.schema.json").unlink()
    with pytest.raises(ConfigNotFoundError) as info:
        loader.load_config()
    assert "project.schema.json" in str(info.value)


def test_invalid_schema_json(config_dir):
    (config_dir / "schemas/project.schema.json").write_text(
        "{invalide", encoding="utf-8"
    )
    with pytest.raises(ConfigSchemaError) as info:
        loader.load_config()
    assert info.value.code == "CONFIG_SCHEMA"
    assert "JSON invalide" in str(info.value)


def test_structurally_invalid_schema(config_dir):
    (config_dir / "schemas/project.schema.json").write_text(
        json.dumps({"type": 123}), encoding="utf-8"
    )
    with pytest.raises(ConfigSchemaError) as info:
        loader.load_config()
    assert "schéma JSON invalide" in str(info.value)


# ==========================================
# VALIDATION
# ==========================================

def test_multiple_validation_errors_are_aggregated(config_dir):
    data = read_config(config_dir)
    data["project"].pop("name")
    data["project"]["environment"] = "unknown"
    write_config(config_dir, data)

    with pytest.raises(ConfigValidationError) as info:
        loader.load_config()

    exc = info.value
    assert isinstance(exc, ValidationError)
    assert exc.code == "CONFIG_VALIDATION"
    assert len(exc.issues) >= 2

    text = str(exc)
    assert "$.project" in text
    assert "$.project.environment" in text
    assert "development" in text
    assert str(config_dir / "project.yaml") in text


def test_validation_error_has_json_path_and_hint(config_dir):
    data = read_config(config_dir)
    data["project"]["version"] = 1.0
    write_config(config_dir, data)

    with pytest.raises(ConfigValidationError) as info:
        loader.load_config()

    assert "$.project.version" in str(info.value)
    assert "guillemets" in str(info.value)


def test_unknown_field_is_reported(config_dir):
    data = read_config(config_dir)
    data["project"]["unknown_field"] = True
    write_config(config_dir, data)

    with pytest.raises(ConfigValidationError) as info:
        loader.load_config()
    assert "unknown_field" in str(info.value)
