"""Rôles locaux et liste fermée de permissions."""
from __future__ import annotations

READ = frozenset({"training:read", "config:read", "project:read"})
WRITE = READ | frozenset({"training:validate", "training:create", "config:write", "project:write"})
ADMIN = WRITE | frozenset({"project:manage", "token:manage"})

ROLES: dict[str, frozenset[str]] = {
    "viewer": READ,
    "editor": WRITE,
    "admin": ADMIN,
}


def permissions_for(role: str) -> frozenset[str]:
    """Retourner les permissions d'un rôle connu, ou un ensemble vide sinon."""
    return ROLES.get(role, frozenset())
