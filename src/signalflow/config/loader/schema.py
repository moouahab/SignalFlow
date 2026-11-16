"""Chargement du schéma JSON et validation de la configuration.

Le module produit un ``ValidationReport`` immuable : la décision de lever
une exception appartient à l'orchestrateur (``loader.load_config``).
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from . import files, messages
from .errors import (
    ConfigSchemaError,
    ConfigValidationError,
    ValidationIssue,
)


@dataclass(frozen=True)
class ValidationReport:
    """Résultat immuable d'une validation de configuration."""
    issues: tuple[ValidationIssue, ...] = ()
    @property
    def is_valid(self) -> bool:
        return not self.issues
    def raise_if_invalid(self, file: Path) -> None:
        if self.is_valid:
            return
        raise ConfigValidationError(
            messages.render_issues(self.issues),
            file=file,
            issues=self.issues,
        )


def _parse_json(text: str, path: Path) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigSchemaError(
            f"JSON invalide (ligne {exc.lineno}, colonne {exc.colno}) : "
            f"{exc.msg}",
            file=path,
        ) from exc


def _check_structure(schema: Any, path: Path) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ConfigSchemaError(
            f"schéma JSON invalide : {exc.message}", file=path
        ) from exc


def load_schema(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Lire, parser et vérifier structurellement le schéma JSON."""
    text = files.read_text(path, max_bytes=max_bytes, encoding="utf-8-sig")
    schema = _parse_json(text, path)
    _check_structure(schema, path)
    return schema


def _sort_key(error: ValidationError) -> tuple[str, ...]:
    return tuple(map(str, error.absolute_path))


def validate(
    instance: Any,
    schema: dict[str, Any],
    *,
    schema_path: Path,
) -> ValidationReport:
    """Valider une instance et collecter toutes les violations.

    :param instance: document issu du chargeur YAML.
    :param schema: schéma JSON structurellement vérifié.
    :param schema_path: chemin utilisé pour localiser les erreurs de schéma.
    :returns: rapport ordonné des violations, vide si le document est valide.
    :raises ConfigSchemaError: référence externe ou interne non résolue.
    :side effects: aucun accès réseau ou fichier ; seules les références internes
        au schéma sont autorisées.
    """
    # Les schémas locaux ne doivent déclencher ni téléchargement distant ni
    # lecture arbitraire via $ref. Les références internes (#/$defs/...) restent
    # résolues ; les références externes produisent une ConfigSchemaError.
    validator = Draft202012Validator(schema, registry=Registry())

    try:
        errors = list(validator.iter_errors(instance))
    except Unresolvable as exc:
        raise ConfigSchemaError(
            f"référence non résolue dans le schéma : {exc}",
            file=schema_path,
        ) from exc

    errors.sort(key=_sort_key)
    return ValidationReport(tuple(map(messages.issue_from, errors)))


__all__ = ["ValidationReport", "load_schema", "validate"]
