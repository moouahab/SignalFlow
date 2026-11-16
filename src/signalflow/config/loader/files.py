"""Accès fichier sûr : limites, encodage et erreurs typées.

Ce module ne connaît ni YAML ni JSON Schema : il ne manipule que des octets
et du texte, et traduit les erreurs système en erreurs de configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .errors import (
    ConfigEncodingError,
    ConfigError,
    ConfigNotFoundError,
    ConfigReadError,
    ConfigTooLargeError,
)

ErrorBuilder = Callable[[Path, OSError], ConfigError]

def _not_found(path: Path, exc: OSError) -> ConfigError:
    return ConfigNotFoundError(f"fichier introuvable : {path}", file=path)

def _is_directory(path: Path, exc: OSError) -> ConfigError:
    return ConfigReadError(
        f"le chemin est un dossier, pas un fichier : {path}", file=path
    )

def _permission_denied(path: Path, exc: OSError) -> ConfigError:
    return ConfigReadError(f"permission refusée : {path}", file=path)

def _generic(path: Path, exc: OSError) -> ConfigError:
    return ConfigReadError(
        f"lecture impossible ({exc.strerror or exc}) : {path}", file=path
    )

# L'ordre compte : FileNotFoundError doit passer avant OSError.
OS_ERROR_BUILDERS: tuple[tuple[type[OSError], ErrorBuilder], ...] = (
    (FileNotFoundError, _not_found),
    (IsADirectoryError, _is_directory),
    (PermissionError, _permission_denied),
)


def _build_os_error(path: Path, exc: OSError) -> ConfigError:
    builder = next(
        (
            build
            for kind, build in OS_ERROR_BUILDERS
            if isinstance(exc, kind)
        ),
        _generic,
    )
    return builder(path, exc)


def read_bytes(path: Path, *, max_bytes: int) -> bytes:
    """Lire un fichier avec une consommation mémoire bornée.

    :param path: fichier à lire.
    :param max_bytes: maximum d'octets autorisé, positif ou nul.
    :returns: contenu binaire du fichier.
    :raises ValueError: limite négative.
    :raises ConfigError: fichier inaccessible ou dépassant la limite.
    :side effects: ouvre le fichier et lit au plus max_bytes + 1 octets.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes doit être positif ou nul")
    try:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        raise _build_os_error(path, exc) from exc
    if len(data) > max_bytes:
        raise ConfigTooLargeError(
            f"fichier trop volumineux (au moins {len(data)} octets, "
            f"maximum {max_bytes}) : {path}",
            file=path,
            hint="réduire le fichier ou augmenter la limite",
        )
    return data

def read_text(
    path: Path,
    *,
    max_bytes: int,
    encoding: str = "utf-8",
) -> str:
    """Lire un fichier texte en appliquant strictement l'encodage demandé."""
    data = read_bytes(path, max_bytes=max_bytes)
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ConfigEncodingError(
            f"encodage invalide (attendu : {encoding}, octet {exc.start})",
            file=path,
        ) from exc

__all__ = ["read_bytes", "read_text"]
