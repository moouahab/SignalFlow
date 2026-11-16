"""Rendu lisible des erreurs de validation JSON Schema.

Les validateurs sont associés à un formateur via ``FORMATTERS`` : plus de
chaîne de ``if/elif``, il suffit d'ajouter une entrée dans la table.
"""

from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any
from jsonschema.exceptions import ValidationError
from .errors import ValidationIssue


Formatter = Callable[[ValidationError], str]
Predicate = Callable[[ValidationError], bool]


# ==========================================
# TYPES ET CHEMINS
# ==========================================

# L'ordre compte : bool avant int (bool est un sous-type de int).
TYPE_LABELS: tuple[tuple[type, str], ...] = (
    (bool, "booléen"),
    (int, "entier"),
    (float, "nombre"),
    (str, "chaîne"),
    (type(None), "null"),
    (list, "liste"),
    (tuple, "liste"),
    (Mapping, "objet"),
)


def type_name(value: Any) -> str:
    """Nom français d'un type Python, avec repli sur son nom natif."""
    return next(
        (
            label
            for kind, label in TYPE_LABELS
            if isinstance(value, kind)
        ),
        type(value).__name__,
    )


def format_path(path: Iterable[str | int]) -> str:
    """Convertir un chemin jsonschema en ``$.a.b[0]``."""
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in path
    )


# ==========================================
# FORMATEURS PAR VALIDATEUR
# ==========================================

def _required(err: ValidationError) -> str:
    if not isinstance(err.instance, Mapping):
        return err.message

    missing = sorted(set(err.validator_value) - set(err.instance))
    if not missing:
        return err.message

    return "champ(s) obligatoire(s) manquant(s) : " + ", ".join(
        map(repr, missing)
    )


def _type(err: ValidationError) -> str:
    expected = err.validator_value
    if isinstance(expected, list):
        expected = " ou ".join(expected)

    return (
        f"type attendu '{expected}', "
        f"reçu '{type_name(err.instance)}'"
    )


def _enum(err: ValidationError) -> str:
    allowed = ", ".join(map(repr, err.validator_value))
    return f"valeur {err.instance!r} non autorisée (attendu : {allowed})"


def _const(err: ValidationError) -> str:
    return (
        f"valeur {err.instance!r} invalide "
        f"(attendu : {err.validator_value!r})"
    )


def _pattern(err: ValidationError) -> str:
    return f"format invalide (motif attendu : {err.validator_value})"


def _min_length(err: ValidationError) -> str:
    return (
        f"longueur minimale {err.validator_value} "
        f"(reçu {len(err.instance)})"
    )


def _min_items(err: ValidationError) -> str:
    return (
        f"au moins {err.validator_value} élément(s) attendu(s) "
        f"(reçu {len(err.instance)})"
    )


def _unique_items(err: ValidationError) -> str:
    return "les éléments doivent être uniques"


def _additional_properties(err: ValidationError) -> str:
    if not (
        isinstance(err.instance, Mapping)
        and isinstance(err.schema, Mapping)
    ):
        return err.message

    known = set(err.schema.get("properties", {}))
    unknown = sorted(set(err.instance) - known)
    if not unknown:
        return err.message

    return "champ(s) non autorisé(s) : " + ", ".join(map(repr, unknown))


FORMATTERS: dict[str, Formatter] = {
    "required": _required,
    "type": _type,
    "enum": _enum,
    "const": _const,
    "pattern": _pattern,
    "minLength": _min_length,
    "minItems": _min_items,
    "uniqueItems": _unique_items,
    "additionalProperties": _additional_properties,
}


def message_for(err: ValidationError) -> str:
    """Message français associé à une violation (repli sur le message natif)."""
    formatter = FORMATTERS.get(err.validator or "")
    return formatter(err) if formatter is not None else err.message


# ==========================================
# AIDES CONTEXTUELLES
# ==========================================

IMPLICIT_YAML_TYPES = (bool, int, float, date, datetime)


def _is_implicit_yaml_type(err: ValidationError) -> bool:
    return (
        err.validator == "type"
        and isinstance(err.instance, IMPLICIT_YAML_TYPES)
    )


HINT_RULES: tuple[tuple[Predicate, str], ...] = (
    (
        _is_implicit_yaml_type,
        "valeur interprétée par YAML (booléen/nombre/date) : "
        "la mettre entre guillemets dans le fichier",
    ),
)


def hint_for(err: ValidationError) -> str | None:
    return next(
        (hint for predicate, hint in HINT_RULES if predicate(err)),
        None,
    )


# ==========================================
# CONVERSION ET RENDU
# ==========================================

def issue_from(err: ValidationError) -> ValidationIssue:
    """Normaliser une violation jsonschema en ``ValidationIssue``."""
    return ValidationIssue(
        json_path=format_path(err.absolute_path),
        message=message_for(err),
        instance=err.instance,
        validator=err.validator,
        hint=hint_for(err),
    )


def _render_issue(issue: ValidationIssue) -> str:
    line = f"  - {issue.json_path}: {issue.message}"
    if issue.hint:
        return f"{line}\n    aide : {issue.hint}"
    return line


def render_issues(issues: Iterable[ValidationIssue]) -> str:
    """Rendre un rapport multi-erreurs prêt à afficher."""
    issues = tuple(issues)
    body = "\n".join(map(_render_issue, issues))
    return f"{len(issues)} erreur(s) de validation :\n{body}"


__all__ = [
    "format_path",
    "type_name",
    "message_for",
    "hint_for",
    "issue_from",
    "render_issues",
]
