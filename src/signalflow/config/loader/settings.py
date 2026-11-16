"""Chemins, limites et résolution du dossier de configuration."""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

ENV_CONFIG_DIR = "SIGNALFLOW_CONFIG"
DEFAULT_CONFIG_DIRNAME = "config"

def default_config_dir() -> Path:
    """Dossier de configuration par défaut.
    Priorité : variable d'environnement ``SIGNALFLOW_CONFIG``, puis
    ``./config`` relatif au répertoire courant. Le paquet installé ne
    contient aucune configuration utilisateur.
    """
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / DEFAULT_CONFIG_DIRNAME).resolve()


@dataclass(frozen=True)
class Limits:
    """Tailles maximales acceptées pour chaque fichier (en octets)."""
    config_bytes: int = 1_048_576
    schema_bytes: int = 1_048_576

@dataclass(frozen=True)
class ConfigPaths:
    """Localisation des fichiers de configuration d'un projet."""
    base: Path
    project_name: str = "project.yaml"
    schema_name: str = "schemas/project.schema.json"
    @classmethod
    def from_dir(cls, base: str | Path) -> Self:
        return cls(base=Path(base))
    @property
    def project(self) -> Path:
        return self.base / self.project_name
    @property
    def schema(self) -> Path:
        return self.base / self.schema_name
