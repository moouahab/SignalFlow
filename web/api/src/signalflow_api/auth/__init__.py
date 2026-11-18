"""Identité locale et permissions : aucun mode anonyme pour les routes métier."""

from .identity import (
    DatabaseTokenResolver,
    Principal,
    StaticTokenResolver,
    TokenResolver,
    bearer_token,
    hash_token,
    static_resolver_from_spec,
)
from .policy import ROLES, permissions_for

__all__ = [
    "Principal",
    "ROLES",
    "DatabaseTokenResolver",
    "StaticTokenResolver",
    "TokenResolver",
    "bearer_token",
    "hash_token",
    "permissions_for",
    "static_resolver_from_spec",
]
