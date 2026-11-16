"""Validation structurelle et sémantique des sélections d'outils."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from signalflow.tools import ToolDeclaration, ToolRegistry, ToolUnknownError

from . import schema, yaml_loader
from .errors import ConfigPathNotFoundError
from .paths import resolve_path
from .settings import Limits


def load_tools(
    reference: dict[str, Any],
    *,
    base_dir: Path,
    limits: Limits,
    registry: ToolRegistry,
) -> tuple[ToolDeclaration, ...]:
    """Charger la référence config.tools d'un projet déjà validé.

    Args:
        reference: déclaration path/schema/required et enabled optionnel.
        base_dir: dossier du projet, base unique des références relatives.
        limits: limites des lectures YAML et JSON.
        registry: catalogue autorisé ; aucune factory n'est appelée.
    Returns:
        Déclarations immuables dans l'ordre YAML. Tuple vide si la section est
        désactivée, ou si son fichier est absent et required vaut false.
    Raises:
        ConfigError: lecture, syntaxe, schéma, chemin ou validation incorrects.
        ToolUnknownError: fournisseur non enregistré dans sa catégorie.
    Side effects:
        Lit tools.yaml et son schéma une fois. Vérifie l'existence des références
        config, même pour un outil désactivé ; ne lit pas leurs paramètres métier.
        Une section config.tools désactivée n'entraîne aucune lecture de ses fichiers.
    """
    if not reference.get("enabled", True):
        return ()
    try:
        path = resolve_path(reference["path"], base_dir=base_dir)
    except ConfigPathNotFoundError:
        if reference["required"]:
            raise
        return ()
    schema_path = resolve_path(reference["schema"], base_dir=base_dir)
    contract = schema.load_schema(schema_path, max_bytes=limits.schema_bytes)
    document = yaml_loader.load_project(path, max_bytes=limits.config_bytes)
    schema.validate(document, contract, schema_path=schema_path).raise_if_invalid(path)

    declarations = []
    for category, selection in document["tools"].items():
        try:
            registry.resolve(category, selection["provider"])
        except ToolUnknownError:
            # Ne jamais inclure les valeurs utilisateur dans ce diagnostic.
            raise ToolUnknownError(
                "fournisseur non autorisé pour cette catégorie",
                file=path,
                config_path=f"$.tools.{category}.provider",
                hint="vérifier le catalogue du registre ou enregistrer un adaptateur",
            ) from None
        config = (
            resolve_path(selection["config"], base_dir=base_dir)
            if "config" in selection else None
        )
        declarations.append(ToolDeclaration(
            category=category,
            provider=selection["provider"],
            enabled=selection["enabled"],
            config=config,
        ))
    return tuple(declarations)
