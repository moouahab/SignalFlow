"""Points d'extension d'identité, base et permissions ; aucun mode anonyme métier."""
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import sessionmaker

from .auth.identity import Principal, TokenResolver, bearer_token
from .errors import ApiError


def get_principal(request: Request) -> Principal:
    """Authentifier le jeton Bearer via le résolveur fourni par l'opérateur."""
    resolver: TokenResolver | None = getattr(request.app.state, "token_resolver", None)
    if resolver is None:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "Le fournisseur d'identité n'est pas configuré.")
    principal = resolver.resolve(bearer_token(request))
    if principal is None or not principal.permissions:
        raise ApiError(401, "INVALID_TOKEN", "Le jeton est invalide ou révoqué.")
    return principal


@dataclass(frozen=True)
class CurrentUser:
    identity: str
    subject: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == 'admin'


def get_session_factory(request: Request) -> sessionmaker:
    factory = getattr(request.app.state, 'session_factory', None)
    if factory is None:
        raise ApiError(503, 'DATABASE_NOT_CONFIGURED', 'Base applicative absente.')
    return factory


def get_current_user(principal: Principal = Depends(get_principal),
                     factory: sessionmaker = Depends(get_session_factory)) -> CurrentUser:
    from signalflow.persistence.repositories import UserRepository
    from signalflow.persistence.session import session_scope

    with session_scope(factory) as session:
        repository = UserRepository(session)
        user = repository.get(principal.user_id) if principal.user_id else None
        if user is None:
            user = repository.ensure(principal.subject, principal.role)
        return CurrentUser(identity=user.id, subject=user.subject, role=user.role)


def require_permission(permission: str) -> Callable:
    """Construire une dépendance remplaçable sans coupler les routes au RBAC."""
    def check(principal: Principal = Depends(get_principal)) -> Principal:
        if permission not in principal.permissions:
            raise ApiError(403, "PERMISSION_DENIED", "Permission insuffisante.")
        return principal
    return check


__all__ = [
    "CurrentUser", "Principal", "TokenResolver", "get_current_user",
    "get_principal", "get_session_factory", "require_permission",
]
