"""Authentification par jetons locaux : le jeton brut n'est jamais conservé."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import hmac
import re
from typing import Protocol

from fastapi import Request

from ..errors import ApiError
from .policy import ROLES, permissions_for

_BEARER = re.compile(r"^Bearer ([A-Za-z0-9._~-]{8,256})$")


@dataclass(frozen=True)
class Principal:
    """Identité authentifiée et rôle global, sans donnée fournie par le client."""

    subject: str
    permissions: frozenset[str]
    role: str = "viewer"
    user_id: str | None = None


class TokenResolver(Protocol):
    """Source de vérité des jetons ; l'implémentation est fournie par l'opérateur."""

    def resolve(self, raw_token: str) -> Principal | None:
        """Retourner l'identité associée au jeton, ou None si invalide/révoqué."""
        ...


def hash_token(raw_token: str) -> str:
    """Empreinte SHA-256 d'un jeton à haute entropie, sans sel secret réutilisable."""
    return sha256(raw_token.encode("utf-8")).hexdigest()


def bearer_token(request: Request) -> str:
    """Extraire un seul jeton Bearer ; refuser toute autre forme d'autorisation."""
    header = request.headers.get("authorization")
    if not header:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "Un jeton Bearer est requis.")
    match = _BEARER.fullmatch(header)
    if match is None:
        raise ApiError(401, "INVALID_AUTHORIZATION", "Le jeton est invalide.")
    return match.group(1)


class DatabaseTokenResolver:
    """Résolveur branché sur le dépôt applicatif, sans dépendance ORM dans l'API."""

    def __init__(self, lookup) -> None:
        self.lookup = lookup

    def resolve(self, raw_token: str) -> Principal | None:
        """Chercher l'empreinte puis reconstruire permissions et rôle persistés."""
        record = self.lookup(hash_token(raw_token))
        if record is None:
            return None
        role = record["role"]
        permissions = permissions_for(role)
        if not permissions:
            return None
        return Principal(subject=record["subject"], permissions=permissions,
                         role=role, user_id=record["user_id"])


@dataclass(frozen=True)
class StaticTokenResolver:
    """Résolveur en mémoire pour l'exploitation locale et les tests."""

    principals: Mapping[str, Principal]

    def resolve(self, raw_token: str) -> Principal | None:
        """Comparer l'empreinte en temps constant, sans journaliser le jeton."""
        digest = hash_token(raw_token)
        for known, principal in self.principals.items():
            if hmac.compare_digest(digest, known):
                return principal
        return None


def static_resolver_from_spec(spec: str) -> StaticTokenResolver:
    """Construire un résolveur depuis ``role:jeton,role:jeton`` fourni à l'opérateur.

    Les jetons sont hachés immédiatement ; seules les empreintes restent en mémoire.
    """
    principals: dict[str, Principal] = {}
    for item in spec.split(","):
        role, separator, token = item.strip().partition(":")
        if not separator or not token or role not in ROLES:
            raise ValueError("SIGNALFLOW_API_TOKENS mal formé")
        if _BEARER.fullmatch(f"Bearer {token}") is None:
            raise ValueError("jeton local trop court ou invalide")
        digest = hash_token(token)
        principals[digest] = Principal(
            subject=f"local-{role}-{digest[:12]}",
            permissions=permissions_for(role),
            role=role,
            user_id=None,
        )
    if not principals:
        raise ValueError("aucun jeton local fourni")
    return StaticTokenResolver(principals)
