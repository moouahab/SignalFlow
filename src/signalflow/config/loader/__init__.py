"""Chargement de la configuration SignalFlow.

Points d'entrée publics : :func:`load_config` et :func:`load_configuration`.

Le paquet ne contient aucune configuration utilisateur : le dossier est
fourni explicitement via ``config_dir``, sinon résolu par la variable
``SIGNALFLOW_CONFIG``, sinon ``./config`` depuis le répertoire courant.

Responsabilités réparties dans les sous-modules :

- ``files``       : lecture sûre (taille, encodage, erreurs système) ;
- ``yaml_loader`` : parsing YAML strict (doublons, syntaxe, racine) ;
- ``schema``      : schéma JSON et rapport de validation ;
- ``messages``    : rendu des erreurs de validation ;
- ``errors``      : exceptions et structures d'erreur ;
- ``settings``    : chemins, limites et résolution du dossier.
- ``tools``       : validation des déclarations et références d'outils.

Exemple :
    >>> from signalflow.config.loader import load_config
    >>> config = load_config(config_dir="/etc/signalflow")
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING
from . import schema as schema_module
from . import yaml_loader
from .settings import ConfigPaths, Limits, default_config_dir

if TYPE_CHECKING:
    from signalflow.tools import ToolDeclaration, ToolRegistry


CONFIG = default_config_dir()
DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class LoadedConfiguration:
    """Projet original et sélections validées, sans instances de fournisseurs."""

    project: dict[str, Any]
    tools: tuple[ToolDeclaration, ...]


def load_configuration(
    config_dir: str | Path | None = None,
    *,
    limits: Limits = DEFAULT_LIMITS,
    registry: ToolRegistry | None = None,
) -> LoadedConfiguration:
    """Charger le projet et ses déclarations d'outils sans les initialiser.

    :param config_dir: dossier de configuration explicite (prioritaire).
    :param limits: tailles maximales des fichiers lus.
    :param registry: catalogue autorisé, ou catalogue intégré sans adaptateurs.
    :returns: projet original et déclarations d'outils validées.
    :raises ConfigError: erreur de lecture, syntaxe, schéma, chemin ou fournisseur.
    :side effects: lit chaque configuration et schéma une fois ; aucun SDK chargé.
    """
    # Imports locaux : les erreurs du loader sont également utilisées par le
    # registre ; conserver leur import possible indépendamment de l'orchestrateur.
    from signalflow.tools import ToolRegistry
    from .tools import load_tools

    base = (
        Path(config_dir).expanduser().resolve()
        if config_dir is not None
        else CONFIG
    )
    paths = ConfigPaths.from_dir(base)

    schema = schema_module.load_schema(
        paths.schema,
        max_bytes=limits.schema_bytes,
    )
    project = yaml_loader.load_project(
        paths.project,
        max_bytes=limits.config_bytes,
    )
    report = schema_module.validate(
        project,
        schema,
        schema_path=paths.schema,
    )
    report.raise_if_invalid(paths.project)
    tools = load_tools(
        project["config"]["tools"],
        base_dir=base,
        limits=limits,
        registry=registry if registry is not None else ToolRegistry(),
    )
    return LoadedConfiguration(project, tools)


def load_config(
    config_dir: str | Path | None = None,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Charger et valider le projet, y compris sa référence tools.

    :param config_dir: dossier explicite, sinon SIGNALFLOW_CONFIG ou ./config.
    :param limits: tailles maximales des fichiers YAML et JSON.
    :returns: dictionnaire project.yaml inchangé, compatible avec l'API existante.
    :raises ConfigError: erreur de lecture, syntaxe, schéma, chemin ou fournisseur.
    :side effects: lecture des fichiers ; aucun fournisseur initialisé.
    """
    return load_configuration(config_dir, limits=limits).project


__all__ = [
    "load_config",
    "load_configuration",
    "LoadedConfiguration",
    "CONFIG",
    "DEFAULT_LIMITS",
    "ConfigPaths",
    "Limits",
    "default_config_dir",
]
