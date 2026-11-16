"""Exceptions publiques du chargeur de configuration SignalFlow.

Chaque erreur expose un contrat stable :

- ``code``      : identifiant exploitable par un CLI ou un rapport JSON ;
- ``file``      : fichier concerné (chemin absolu lorsque fourni) ;
- ``config_path`` : emplacement dans la configuration (``$.project.version``) ;
- ``hint``      : piste de correction éventuelle ;
- ``issues``    : détail normalisé pour ``ConfigValidationError``.

Les classes héritent aussi de l'exception native correspondante
(``FileNotFoundError``, ``OSError``, ``yaml.YAMLError``,
``jsonschema.ValidationError``) afin de ne pas casser les appelants et les
tests existants.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import yaml
from jsonschema.exceptions import ValidationError


@dataclass(frozen=True)
class ValidationIssue:
    """Une violation de schéma normalisée et lisible."""

    json_path: str
    message: str
    instance: Any = None
    validator: str | None = None
    hint: str | None = None


class ConfigError(Exception):
    """Erreur de base du chargement de configuration."""

    code = "CONFIG_ERROR"

    def __init__(
        self,
        message: str,
        *,
        file: str | Path | None = None,
        config_path: str | None = None,
        hint: str | None = None,
        issues: Iterable[ValidationIssue] = (),
    ) -> None:
        self.message = message
        self.file = Path(file).resolve() if file is not None else None
        self.config_path = config_path
        self.hint = hint
        self.issues: tuple[ValidationIssue, ...] = tuple(issues)
        super().__init__(message)

    def __str__(self) -> str:
        header = f"[{self.code}]"
        if self.file is not None:
            header += f" {self.file}"
        if self.config_path is not None:
            header += f" → {self.config_path}"
        text = f"{header}: {self.message}"
        if self.hint:
            text += f"\n  aide : {self.hint}"
        return text


class ConfigNotFoundError(ConfigError, FileNotFoundError):
    """Fichier ou dossier de configuration introuvable."""

    code = "CONFIG_NOT_FOUND"


class ConfigReadError(ConfigError, OSError):
    """Erreur d'accès au fichier (dossier, permissions, I/O)."""

    code = "CONFIG_READ"


class ConfigEncodingError(ConfigReadError):
    """Le fichier n'est pas encodé en UTF-8."""

    code = "CONFIG_ENCODING"


class ConfigTooLargeError(ConfigReadError):
    """Le fichier dépasse la taille maximale autorisée."""

    code = "CONFIG_TOO_LARGE"


class ConfigSyntaxError(ConfigError, yaml.YAMLError):
    """Le contenu YAML est mal formé."""

    code = "CONFIG_SYNTAX"


class ConfigDuplicateKeyError(ConfigSyntaxError):
    """Une clé est définie plusieurs fois dans un même mapping."""

    code = "CONFIG_DUPLICATE_KEY"


class ConfigEmptyError(ConfigSyntaxError):
    """Le fichier de configuration est vide."""

    code = "CONFIG_EMPTY"


class ConfigSchemaError(ConfigError):
    """Le schéma JSON est absent, illisible ou invalide."""

    code = "CONFIG_SCHEMA"


class ConfigValidationError(ConfigError, ValidationError):
    """La configuration ne respecte pas son schéma JSON."""

    code = "CONFIG_VALIDATION"


class ConfigPathError(ConfigError):
    """Chemin référencé refusé par le résolveur sécurisé."""

    code = "CONFIG_PATH"

    def __init__(
        self,
        message: str,
        *,
        reference: Any = None,
        reason: str | None = None,
        file: str | Path | None = None,
        hint: str | None = None,
    ) -> None:
        self.reference = reference
        self.reason = reason or message
        super().__init__(message, file=file, hint=hint)


class ConfigPathInvalidError(ConfigPathError):
    """La référence de chemin n'est pas une chaîne relative exploitable."""

    code = "CONFIG_PATH_INVALID"


class ConfigPathAbsoluteError(ConfigPathError):
    """La référence est un chemin absolu."""

    code = "CONFIG_PATH_ABSOLUTE"


class ConfigPathTraversalError(ConfigPathError):
    """La référence sort du dossier autorisé par traversée ``..``."""

    code = "CONFIG_PATH_TRAVERSAL"


class ConfigPathSymlinkError(ConfigPathError):
    """Un lien symbolique conduit hors du dossier autorisé."""

    code = "CONFIG_PATH_SYMLINK"


class ConfigPathNotFoundError(ConfigPathError, FileNotFoundError):
    """Le chemin référencé n'existe pas."""

    code = "CONFIG_PATH_NOT_FOUND"


class ConfigPathNotFileError(ConfigPathError):
    """Le chemin référencé existe mais n'est pas un fichier."""

    code = "CONFIG_PATH_NOT_FILE"


__all__ = [
    "ValidationIssue",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigReadError",
    "ConfigEncodingError",
    "ConfigTooLargeError",
    "ConfigSyntaxError",
    "ConfigDuplicateKeyError",
    "ConfigEmptyError",
    "ConfigSchemaError",
    "ConfigValidationError",
    "ConfigPathError",
    "ConfigPathInvalidError",
    "ConfigPathAbsoluteError",
    "ConfigPathTraversalError",
    "ConfigPathSymlinkError",
    "ConfigPathNotFoundError",
    "ConfigPathNotFileError",
]
