"""Configuration Ollama validée, sans accès réseau ni secret persisté."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from signalflow.config import loader
from signalflow.config.loader import schema, yaml_loader
from signalflow.config.loader.errors import ConfigError

from .errors import OllamaConfigError


@dataclass(frozen=True)
class LLMConfiguration:
    """Documents validés et déclaration Ollama active.

    Les dictionnaires sont ceux du YAML validé, sans valeur secrète :
    ``api_key_env`` ne désigne qu'un nom de variable d'environnement.
    """

    base: Path
    tools: tuple[Any, ...]
    ollama: dict[str, Any]
    tracking: dict[str, Any]
    sources: dict[str, str]


def _validate_url(value: str, *, label: str, schemes: set[str], allow_path: bool) -> str:
    """Valider une URL sans information d'authentification ni charge utile."""
    try:
        parts = urlsplit(value)
    except ValueError:
        raise OllamaConfigError(f"{label} invalide", config_path=f"$.{label}") from None
    if parts.scheme not in schemes or (parts.scheme in {"http", "https"} and not parts.hostname):
        raise OllamaConfigError(
            f"{label} doit utiliser un schéma autorisé et, pour HTTP, un hôte", config_path=f"$.{label}",
        )
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise OllamaConfigError(
            f"{label} ne peut contenir ni identifiants ni requête", config_path=f"$.{label}",
        )
    if parts.path and (not allow_path or not parts.path.startswith("/")):
        raise OllamaConfigError(f"{label} contient un chemin interdit", config_path=f"$.{label}")
    return value


def validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Contrôler les contraintes que le schéma JSON ne peut pas exprimer.

    Args:
        settings: document llm.yaml déjà validé structurellement.
    Returns:
        Le document inchangé, avec une URL de base normalisée sans slash final.
    Raises:
        OllamaConfigError: URL invalide, schéma interdit ou identifiants dans l'URI.
    Side effects:
        Aucun, notamment aucun accès réseau.
    """
    ollama = settings["ollama"]
    tracking = settings["tracking"]
    base_url = _validate_url(
        ollama["base_url"], label="ollama.base_url", schemes={"http", "https"}, allow_path=True,
    )
    ollama["base_url"] = base_url.rstrip("/")
    if "tracking_uri" in tracking:
        _validate_url(
            tracking["tracking_uri"], label="tracking.tracking_uri",
            schemes={"http", "https", "sqlite", "file"}, allow_path=True,
        )
    return settings


def load_llm_settings(path: Path, *, limits: loader.Limits = loader.DEFAULT_LIMITS) -> dict[str, Any]:
    """Lire et valider un document llm.yaml avec son schéma local.

    Args:
        path: chemin absolu du document, résolu par le chargeur de configuration.
        limits: tailles maximales autorisées pour le YAML et le schéma.
    Returns:
        Paramètres Ollama et de suivi, sans secret en clair.
    Raises:
        ConfigError: lecture, syntaxe, schéma ou sémantique invalide.
    Side effects:
        Deux lectures locales uniquement ; aucune initialisation de client.
    """
    schema_path = path.parent / "schemas" / "llm.schema.json"
    contract = schema.load_schema(schema_path, max_bytes=limits.schema_bytes)
    document = yaml_loader.load_project(path, max_bytes=limits.config_bytes)
    schema.validate(document, contract, schema_path=schema_path).raise_if_invalid(path)
    return validate_settings(document)


def load_llm_configuration(
    config_dir: str | Path | None = None, *,
    registry: Any,
    limits: loader.Limits = loader.DEFAULT_LIMITS,
) -> LLMConfiguration:
    """Charger le projet et la déclaration Ollama active.

    Args:
        config_dir: dossier contenant project.yaml ; None pour le dossier par défaut.
        registry: registre autorisé, utilisé uniquement pour résoudre la sélection.
        limits: limites de lecture des configurations.
    Returns:
        LLMConfiguration avec les réglages Ollama et MLflow déjà validés.
    Raises:
        ConfigError: configuration projet ou tools invalide.
        OllamaConfigError: fournisseur Ollama absent, désactivé ou mal référencé.
    Side effects:
        Lectures locales uniquement ; aucune factory appelée, aucun SDK importé.
    """
    base = Path(config_dir).expanduser().resolve() if config_dir is not None else loader.CONFIG
    loaded = loader.load_configuration(base, registry=registry, limits=limits)
    declarations = tuple(item for item in loaded.tools if item.category == "llm")
    if len(declarations) != 1:
        raise OllamaConfigError(
            "exactement une déclaration llm est requise", config_path="$.tools.llm",
        )
    declaration = declarations[0]
    if declaration.provider != "ollama" or not declaration.enabled:
        raise OllamaConfigError(
            "le fournisseur llm doit être ollama et activé", config_path="$.tools.llm",
            hint="activer llm dans tools.yaml",
        )
    if declaration.config is None:
        raise OllamaConfigError(
            "référence de configuration Ollama absente", config_path="$.tools.llm.config",
        )
    try:
        settings = load_llm_settings(declaration.config, limits=limits)
    except ConfigError as error:
        raise OllamaConfigError(
            error.message, file=error.file, config_path=error.config_path, hint=error.hint,
        ) from None
    return LLMConfiguration(
        base=base, tools=loaded.tools, ollama=settings["ollama"],
        tracking=settings["tracking"], sources={"llm": str(declaration.config)},
    )
