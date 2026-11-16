"""Tests du résolveur sécurisé des chemins de configuration.

Les tentatives d'évasion (chemin absolu, ``..``, lien symbolique externe)
doivent être refusées ; les chemins légitimes du dossier de configuration
doivent être acceptés, y compris depuis un répertoire extérieur au dépôt.
"""

from pathlib import Path

import pytest

from signalflow.config.loader.errors import (
    ConfigPathAbsoluteError,
    ConfigPathError,
    ConfigPathInvalidError,
    ConfigPathNotFoundError,
    ConfigPathNotFileError,
    ConfigPathSymlinkError,
    ConfigPathTraversalError,
)
from signalflow.config.loader.paths import resolve_path


@pytest.fixture
def config_dir(tmp_path):
    """Dossier de configuration isolé, hors du dépôt."""
    base = tmp_path / "config"
    (base / "schemas").mkdir(parents=True)
    (base / "sub").mkdir()
    (base / "project.yaml").write_text(
        "schema_version: '0.0.1'\n", encoding="utf-8"
    )
    (base / "model.yaml").write_text("model: {}\n", encoding="utf-8")
    (base / "sub" / "nested.yaml").write_text(
        "nested: {}\n", encoding="utf-8"
    )
    (base / "schemas" / "model.schema.json").write_text(
        "{}\n", encoding="utf-8"
    )
    return base


@pytest.fixture
def outside_dir(tmp_path):
    """Fichiers hors du dossier autorisé, cibles d'évasion."""
    base = tmp_path / "outside"
    base.mkdir()
    (base / "secret.yaml").write_text("secret: true\n", encoding="utf-8")
    (base / "secret.schema.json").write_text("{}\n", encoding="utf-8")
    return base


# ==========================================
# CHEMINS AUTORISÉS
# ==========================================

@pytest.mark.parametrize(
    "reference",
    [
        "./model.yaml",
        "model.yaml",
        "./sub/nested.yaml",
        "./sub/./nested.yaml",
        "./sub/../model.yaml",
    ],
)
def test_relative_path_inside_is_accepted(config_dir, reference):
    """Un chemin relatif confiné est résolu depuis le dossier de config."""
    resolved = resolve_path(reference, base_dir=config_dir)

    assert resolved.is_file()
    assert resolved.is_relative_to(config_dir.resolve())


# ==========================================
# CHEMINS ABSOLUS
# ==========================================

@pytest.mark.parametrize("location", ["inside", "outside"])
def test_absolute_path_is_rejected(config_dir, outside_dir, location):
    """Tout chemin absolu est refusé, même s'il pointe dans le dossier."""
    reference = str(
        config_dir / "model.yaml"
        if location == "inside"
        else outside_dir / "secret.yaml"
    )

    with pytest.raises(ConfigPathAbsoluteError) as info:
        resolve_path(reference, base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_ABSOLUTE"
    assert info.value.reference == reference
    assert "absolu" in info.value.message


# ==========================================
# TRAVERSÉES
# ==========================================

@pytest.mark.parametrize(
    "reference",
    [
        "../outside/secret.yaml",
        "./sub/../../outside/secret.yaml",
        "./sub/../../outside/missing.yaml",
    ],
)
def test_traversal_outside_is_rejected(config_dir, outside_dir, reference):
    """Une traversée ``..`` qui sort du dossier est refusée."""
    with pytest.raises(ConfigPathTraversalError) as info:
        resolve_path(reference, base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_TRAVERSAL"
    assert info.value.reference == reference
    assert str(config_dir.resolve()) in info.value.reason


def test_sibling_with_common_prefix_is_rejected(config_dir, tmp_path):
    """Un voisin ``config-evil`` n'est pas confondu avec ``config``."""
    sibling = tmp_path / "config-evil"
    sibling.mkdir()
    (sibling / "secret.yaml").write_text("secret: true\n", encoding="utf-8")

    with pytest.raises(ConfigPathTraversalError):
        resolve_path("../config-evil/secret.yaml", base_dir=config_dir)


# ==========================================
# LIENS SYMBOLIQUES
# ==========================================

def test_internal_symlink_is_accepted(config_dir):
    """Un lien symbolique interne au dossier reste autorisé."""
    link = config_dir / "alias.yaml"
    link.symlink_to(config_dir / "model.yaml")

    resolved = resolve_path("./alias.yaml", base_dir=config_dir)

    assert resolved == (config_dir / "model.yaml").resolve()
    assert resolved.is_file()


def test_external_symlink_is_rejected(config_dir, outside_dir):
    """Un lien symbolique vers un fichier externe est refusé."""
    link = config_dir / "leak.yaml"
    link.symlink_to(outside_dir / "secret.yaml")

    with pytest.raises(ConfigPathSymlinkError) as info:
        resolve_path("./leak.yaml", base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_SYMLINK"
    assert str(outside_dir.resolve()) in info.value.message


def test_symlinked_directory_escape_is_rejected(config_dir, outside_dir):
    """Un lien de dossier ne permet pas de sortir du confinement."""
    link = config_dir / "external"
    link.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ConfigPathSymlinkError):
        resolve_path("./external/secret.yaml", base_dir=config_dir)


# ==========================================
# EXISTENCE ET TYPE
# ==========================================

def test_missing_file_is_rejected(config_dir):
    """Un fichier absent est signalé par une erreur typée."""
    with pytest.raises(ConfigPathNotFoundError) as info:
        resolve_path("./missing.yaml", base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_NOT_FOUND"
    assert isinstance(info.value, FileNotFoundError)


def test_directory_is_rejected(config_dir):
    """Un dossier n'est pas une référence de fichier valide."""
    with pytest.raises(ConfigPathNotFileError) as info:
        resolve_path("./sub", base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_NOT_FILE"


# ==========================================
# SCHÉMAS JSON : MÊMES RÈGLES
# ==========================================

@pytest.mark.parametrize(
    "reference",
    [
        "./schemas/model.schema.json",
        "schemas/model.schema.json",
        "./schemas/../schemas/model.schema.json",
    ],
)
def test_schema_relative_reference_is_accepted(config_dir, reference):
    """Un schéma relatif confiné est accepté."""
    resolved = resolve_path(reference, base_dir=config_dir)

    assert resolved.is_file()
    assert resolved.is_relative_to(config_dir.resolve())


def test_schema_traversal_is_rejected(config_dir, outside_dir):
    """Les schémas subissent la même interdiction de traversée."""
    with pytest.raises(ConfigPathTraversalError):
        resolve_path("../outside/secret.schema.json", base_dir=config_dir)


def test_schema_absolute_is_rejected(config_dir, outside_dir):
    """Les schémas subissent la même interdiction des chemins absolus."""
    reference = str(outside_dir / "secret.schema.json")

    with pytest.raises(ConfigPathAbsoluteError):
        resolve_path(reference, base_dir=config_dir)


def test_schema_external_symlink_is_rejected(config_dir, outside_dir):
    """Les schémas subissent la même interdiction des liens externes."""
    link = config_dir / "schemas" / "leak.schema.json"
    link.symlink_to(outside_dir / "secret.schema.json")

    with pytest.raises(ConfigPathSymlinkError):
        resolve_path("./schemas/leak.schema.json", base_dir=config_dir)


# ==========================================
# INDÉPENDANCE DU RÉPERTOIRE COURANT
# ==========================================

def test_resolution_is_independent_from_cwd(
    config_dir, tmp_path, monkeypatch
):
    """La résolution part du dossier de config, pas du ``cwd``."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "model.yaml").write_text(
        "wrong: true\n", encoding="utf-8"
    )
    monkeypatch.chdir(elsewhere)

    resolved = resolve_path("./model.yaml", base_dir=config_dir)

    assert resolved == (config_dir / "model.yaml").resolve()
    assert resolved != (elsewhere / "model.yaml").resolve()


def test_configuration_outside_repository_is_supported(config_dir):
    """Une configuration hors du dépôt est traitée normalement."""
    repo_root = Path(__file__).resolve().parents[2]
    project_file = config_dir / "project.yaml"

    assert not config_dir.resolve().is_relative_to(repo_root)
    resolved = resolve_path("./model.yaml", base_dir=project_file.parent)
    assert resolved == (config_dir / "model.yaml").resolve()
    assert resolved.is_file()


# ==========================================
# CONTRAT D'ERREUR
# ==========================================

def test_error_reports_reference_reason_and_path(config_dir, outside_dir):
    """Chaque refus expose la référence, la raison et le chemin concerné."""
    reference = "../outside/secret.yaml"

    with pytest.raises(ConfigPathError) as info:
        resolve_path(reference, base_dir=config_dir)
    exc = info.value
    assert exc.reference == reference
    assert exc.reason
    assert reference in str(exc)
    assert str(outside_dir.resolve()) in str(exc)


@pytest.mark.parametrize("reference", ["", "   ", "\x00evil", None, 42])
def test_invalid_reference_is_rejected(config_dir, reference):
    """Les références non exploitables sont refusées proprement."""
    with pytest.raises(ConfigPathInvalidError) as info:
        resolve_path(reference, base_dir=config_dir)

    assert info.value.code == "CONFIG_PATH_INVALID"


def test_missing_base_dir_is_rejected(tmp_path):
    """Un dossier de base absent produit une erreur typée."""
    with pytest.raises(ConfigPathError) as info:
        resolve_path("./model.yaml", base_dir=tmp_path / "absent")

    assert info.value.code == "CONFIG_PATH"


def test_base_must_be_directory(tmp_path):
    """Un fichier ne peut pas servir de dossier de configuration."""
    base = tmp_path / "base"
    base.write_text("x\n", encoding="utf-8")

    with pytest.raises(ConfigPathError) as info:
        resolve_path("./model.yaml", base_dir=base)

    assert "n'est pas un dossier" in info.value.message
