"""Parsing YAML strict de la configuration projet.

Responsabilités :

- charger le texte via ``files`` ;
- refuser les clés dupliquées avant que PyYAML ne les écrase ;
- traduire les erreurs de syntaxe en messages positionnés ;
- garantir un mapping à la racine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml

from . import files, messages
from .errors import (
    ConfigDuplicateKeyError,
    ConfigEmptyError,
    ConfigSyntaxError,
)

MERGE_TAG = "tag:yaml.org,2002:merge"

# ==========================================
# CHARGEUR SANS DOUBLONS
# ==========================================

class StrictSafeLoader(yaml.SafeLoader):
    """``SafeLoader`` qui refuse les clés dupliquées dans un mapping."""
    def construct_mapping(self, node, deep=False):
        _reject_duplicate_keys(self, node, deep)
        return super().construct_mapping(node, deep=deep)


def _reject_duplicate_keys(loader, node, deep) -> None:
    if not isinstance(node, yaml.MappingNode):
        return
    duplicate = _first_duplicate(_iter_keys(loader, node, deep))
    if duplicate is None:
        return
    key, mark = duplicate
    raise ConfigDuplicateKeyError(
        f"clé dupliquée {key!r} "
        f"(ligne {mark.line + 1}, colonne {mark.column + 1})"
    )


def _iter_keys(loader, node, deep) -> Iterator[tuple[Any, Any]]:
    """Produire ``(clé, position)`` en ignorant les clés de fusion ``<<``."""
    for key_node, _ in node.value:
        if key_node.tag == MERGE_TAG:
            continue
        yield loader.construct_object(key_node, deep=deep), key_node.start_mark


def _first_duplicate(entries: Iterable[tuple[Any, Any]],) -> tuple[Any, Any] | None:
    seen: set[Any] = set()
    for key, mark in entries:
        try:
            duplicate = key in seen
        except TypeError:
            # Clé non hashable : laissée au schéma pour le diagnostic.
            continue

        if duplicate:
            return key, mark
        seen.add(key)

    return None


# ==========================================
# MESSAGES DE SYNTAXE
# ==========================================

def _describe_composer(exc: yaml.YAMLError) -> str:
    return "un seul document YAML est autorisé par fichier"


def _describe_marked(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return problem
    return (
        f"{problem} "
        f"(ligne {mark.line + 1}, colonne {mark.column + 1})"
    )

YAML_ERROR_DESCRIBERS: tuple[tuple[type[yaml.YAMLError], Callable[[yaml.YAMLError], str]], ...] = (
    (yaml.composer.ComposerError, _describe_composer),
)

def describe_error(exc: yaml.YAMLError) -> str:
    describer = next(
        (
            describe
            for kind, describe in YAML_ERROR_DESCRIBERS
            if isinstance(exc, kind)
        ),
        _describe_marked,
    )
    return describer(exc)

# ==========================================
# POINT D'ENTRÉE DU MODULE
# ==========================================

def _parse(text: str, path: Path) -> Any:
    try:
        return yaml.load(text, Loader=StrictSafeLoader)
    except ConfigDuplicateKeyError as exc:
        exc.file = path.resolve()
        raise
    except yaml.YAMLError as exc:
        raise ConfigSyntaxError(describe_error(exc), file=path) from exc


def _require_mapping(data: Any, path: Path) -> dict[str, Any]:
    if data is None:
        raise ConfigEmptyError(
            "la configuration est vide",
            file=path,
            hint="ajouter au minimum schema_version, project, config et pipeline",
        )
    if not isinstance(data, dict):
        raise ConfigSyntaxError(
            "la racine doit être un objet (mapping YAML), "
            f"reçu {messages.type_name(data)}",
            file=path,
        )
    return data

def load_project(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Lire, parser et normaliser le fichier ``project.yaml``."""
    text = files.read_text(path, max_bytes=max_bytes)
    return _require_mapping(_parse(text, path), path)

__all__ = ["load_project", "describe_error"]
