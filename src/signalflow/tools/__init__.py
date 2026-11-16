"""Sélection déclarative et registre des outils ; aucun SDK externe importé."""

from .registry import (
    Provider,
    ToolDeclaration,
    ToolInitializationError,
    ToolRegistry,
    ToolUnavailableError,
    ToolUnknownError,
)

__all__ = [
    "Provider", "ToolDeclaration", "ToolRegistry", "ToolUnknownError",
    "ToolUnavailableError", "ToolInitializationError",
]
