"""Points d'extension d'identité et permissions ; aucune authentification simulée."""
from dataclasses import dataclass
from collections.abc import Callable

from fastapi import Depends, Request
from .errors import ApiError


@dataclass(frozen=True)
class Principal:
    """Identité fournie par une future dépendance d'authentification fiable."""

    subject: str
    permissions: frozenset[str]


def get_principal(request: Request) -> Principal:
    """Mode local anonyme ; fermer l'accès si une authentification est exigée."""
    if request.app.state.settings.require_authentication:
        raise ApiError(503, 'AUTH_NOT_CONFIGURED', "Le fournisseur d'authentification n'est pas configuré.")
    if 'authorization' in request.headers:
        raise ApiError(401, 'AUTHENTICATION_UNAVAILABLE', "L'authentification n'est pas disponible.")
    return Principal('local-anonymous', frozenset({'training:read', 'training:validate', 'training:create'}))


def require_permission(permission: str) -> Callable:
    """Construire une dépendance remplaçable sans coupler les routes au futur RBAC."""
    def check(principal: Principal = Depends(get_principal)) -> Principal:
        if permission not in principal.permissions:
            raise ApiError(403, 'PERMISSION_DENIED', 'Permission insuffisante.')
        return principal
    return check
