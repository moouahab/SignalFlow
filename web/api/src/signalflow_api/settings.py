"""Paramètres opérateur, jamais construits depuis un document HTTP utilisateur."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Bornes de transport et verrou d'authentification future.

    require_authentication refuse les routes métier tant qu'un fournisseur d'identité
    n'est pas intégré. False désigne le mode local anonyme, pas une authentification.
    """

    max_request_bytes: int = 65536
    body_timeout_seconds: float = 5.0
    max_json_depth: int = 32
    require_authentication: bool = False

    def __post_init__(self) -> None:
        """Refuser des paramètres opérateur hors limites."""
        if not 1024 <= self.max_request_bytes <= 1048576:
            raise ValueError("max_request_bytes hors limites")
        if not 0 < self.body_timeout_seconds <= 30:
            raise ValueError("body_timeout_seconds hors limites")
        if not 1 <= self.max_json_depth <= 64:
            raise ValueError("max_json_depth hors limites")
