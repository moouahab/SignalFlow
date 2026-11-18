"""Erreurs métier de la persistance, sans détail d'implémentation."""
from __future__ import annotations


class PersistenceError(Exception):
    """Base des erreurs traduites par la couche HTTP."""


class NotFoundError(PersistenceError):
    """Ressource absente ou non visible pour l'identité courante."""


class AlreadyExistsError(PersistenceError):
    """Clé projet ou identifiant déjà utilisé."""


class ForbiddenError(PersistenceError):
    """Identité authentifiée mais non membre du projet."""


class VersionConflict(PersistenceError):
    """Une version attendue ne correspond plus à la dernière version publiée."""
