"""Résolution confinée des chemins référencés dans ``project.yaml``.

Ce module ne charge aucune configuration : il transforme une référence
relative (par exemple ``./model.yaml`` ou ``./schemas/model.schema.json``)
en chemin absolu, en garantissant qu'elle reste sous le dossier de
configuration fourni explicitement.

Le dossier de base est toujours explicite : la résolution ne dépend ni du
répertoire courant, ni de l'emplacement d'installation du package.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import (
    ConfigPathAbsoluteError,
    ConfigPathError,
    ConfigPathInvalidError,
    ConfigPathNotFoundError,
    ConfigPathNotFileError,
    ConfigPathSymlinkError,
    ConfigPathTraversalError,
)


def _is_within(path: Path, base: Path) -> bool:
    """Indiquer si ``path`` est ``base`` ou l'un de ses descendants."""
    return path == base or path.is_relative_to(base)


def _check_reference(reference: object) -> str:
    """Refuser les références qui ne peuvent pas être un chemin relatif."""
    if not isinstance(reference, str):
        raise ConfigPathInvalidError(
            f"référence de chemin invalide : {reference!r}",
            reference=reference,
            reason="la référence doit être une chaîne",
        )
    if not reference.strip():
        raise ConfigPathInvalidError(
            "référence de chemin vide",
            reference=reference,
            reason="la référence ne peut pas être vide",
        )
    if "\x00" in reference:
        raise ConfigPathInvalidError(
            "la référence de chemin contient un octet nul",
            reference=reference,
            reason="un octet nul est interdit dans un chemin",
        )
    return reference


def _resolve_base(base_dir: str | Path) -> Path:
    """Résoudre le dossier autorisé et vérifier que c'est un dossier."""
    try:
        base = Path(base_dir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        raise ConfigPathError(
            f"dossier de configuration invalide : {base_dir}",
            reference=base_dir,
            reason=f"impossible de résoudre le dossier de base ({exc})",
            file=base_dir if isinstance(base_dir, (str, Path)) else None,
        ) from exc

    if not base.is_dir():
        raise ConfigPathError(
            f"le dossier de configuration n'est pas un dossier : {base}",
            reference=base_dir,
            reason="la base doit être un dossier",
            file=base,
        )
    return base


def resolve_path(reference: str, *, base_dir: str | Path) -> Path:
    """Résoudre ``reference`` sous ``base_dir`` de façon confinée.

    La référence doit être relative. Les traversées ``..`` qui sortent de
    ``base_dir`` et les liens symboliques qui pointent hors de ce dossier
    sont refusés. Le fichier doit exister et être un fichier régulier.

    Les mêmes règles s'appliquent aux chemins de configuration
    (``config.<nom>.path``) et aux schémas JSON
    (``config.<nom>.schema``).

    :param reference: chemin relatif tel qu'écrit dans ``project.yaml``.
    :param base_dir: dossier contenant ``project.yaml``.
    :returns: chemin absolu résolu, garanti sous ``base_dir``.
    :raises ConfigPathError: référence invalide, absolue, hors dossier,
        introuvable ou non fichier.
    """
    ref = _check_reference(reference)
    base = _resolve_base(base_dir)

    raw = Path(ref)
    if raw.is_absolute():
        raise ConfigPathAbsoluteError(
            f"chemin absolu interdit : {ref!r}",
            reference=ref,
            reason="la référence doit être relative au dossier de configuration",
            file=raw,
            hint="utiliser un chemin tel que ./model.yaml",
        )

    candidate = base / raw
    lexical = Path(os.path.normpath(candidate))

    if not _is_within(lexical, base):
        raise ConfigPathTraversalError(
            f"chemin hors du dossier autorisé : {ref!r}",
            reference=ref,
            reason=f"la référence sort de {base}",
            file=candidate,
            hint="retirer les composants .. qui remontent au-dessus de la configuration",
        )

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigPathInvalidError(
            f"résolution impossible pour {ref!r}",
            reference=ref,
            reason=f"chemin non résolvable ({exc})",
            file=candidate,
        ) from exc

    if not _is_within(resolved, base):
        raise ConfigPathSymlinkError(
            f"lien symbolique hors du dossier autorisé : {ref!r} -> {resolved}",
            reference=ref,
            reason=f"le chemin résolu sort de {base}",
            file=candidate,
            hint="supprimer le lien ou le faire pointer dans le dossier de configuration",
        )

    if not resolved.exists():
        raise ConfigPathNotFoundError(
            f"chemin introuvable : {ref!r}",
            reference=ref,
            reason="le fichier référencé n'existe pas",
            file=resolved,
        )

    if not resolved.is_file():
        raise ConfigPathNotFileError(
            f"le chemin n'est pas un fichier : {ref!r}",
            reference=ref,
            reason="le chemin existe mais n'est pas un fichier régulier",
            file=resolved,
            hint="référencer un fichier, pas un dossier",
        )

    return resolved


__all__ = ["resolve_path"]
