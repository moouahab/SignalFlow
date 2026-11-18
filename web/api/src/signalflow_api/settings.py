"""Paramètres opérateur, jamais construits depuis un document HTTP utilisateur."""
from __future__ import annotations

from dataclasses import dataclass
import os

from .auth.identity import TokenResolver, static_resolver_from_spec


@dataclass(frozen=True)
class Settings:
    """Bornes de transport, identité obligatoire et base applicative optionnelle.

    Aucun champ ne peut être fourni par un document HTTP. Sans résolveur de jetons,
    les routes métier échouent fermées ; ``health`` reste public.
    """

    max_request_bytes: int = 65536
    body_timeout_seconds: float = 5.0
    max_json_depth: int = 32
    token_resolver: TokenResolver | None = None
    database_url: str | None = None

    def __post_init__(self) -> None:
        """Refuser des paramètres opérateur hors limites."""
        if not 1024 <= self.max_request_bytes <= 1048576:
            raise ValueError("max_request_bytes hors limites")
        if not 0 < self.body_timeout_seconds <= 30:
            raise ValueError("body_timeout_seconds hors limites")
        if not 1 <= self.max_json_depth <= 64:
            raise ValueError("max_json_depth hors limites")
        if self.database_url is not None and not self.database_url.strip():
            raise ValueError("database_url vide")

    @classmethod
    def from_env(cls) -> "Settings":
        """Construire les paramètres depuis l'environnement de l'opérateur."""
        tokens = os.environ.get("SIGNALFLOW_API_TOKENS", "").strip()
        return cls(
            token_resolver=static_resolver_from_spec(tokens) if tokens else None,
            database_url=os.environ.get("SIGNALFLOW_DATABASE_URL") or None,
        )
