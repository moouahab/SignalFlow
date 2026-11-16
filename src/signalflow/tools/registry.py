"""Catalogue explicite et initialisation différée, sans import piloté par YAML."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from signalflow.config.loader.errors import ConfigError

# Une seule source pour les fournisseurs : le schéma décrit la structure,
# le registre impose la liste autorisée par catégorie à l'exécution.
KNOWN_PROVIDERS = {
    "data": "pandas",
    "training": "scikit-learn",
    "experiment_tracking": "mlflow",
    "execution": "docker",
    "metrics": "prometheus",
    "logging": "loki",
    "security": "trivy",
}


class ToolUnknownError(ConfigError):
    """La catégorie ou le fournisseur n'est pas enregistré."""

    code = "TOOL_UNKNOWN"


class ToolUnavailableError(ConfigError):
    """Fournisseur connu, sans adaptateur ou dépendance disponible."""

    code = "TOOL_UNAVAILABLE"


class ToolInitializationError(ConfigError):
    """Un adaptateur a échoué sans divulguer ses paramètres internes."""

    code = "TOOL_INITIALIZATION"


@dataclass(frozen=True)
class ToolDeclaration:
    """Sélection validée ; config est un chemin confiné, jamais du code."""

    category: str
    provider: str
    enabled: bool
    config: Path | None = None


ToolFactory = Callable[[ToolDeclaration], object]


@dataclass(frozen=True)
class Provider:
    """Entrée du catalogue : factory=None signifie connu mais indisponible."""

    category: str
    name: str
    factory: ToolFactory | None = None


class ToolRegistry:
    """Registre indépendant par instance, sans initialisation à l'import."""

    def __init__(self) -> None:
        """Créer le catalogue des fournisseurs connus sans adaptateur.

        Aucun paramètre ni retour ; aucune exception attendue ou effet externe.
        Aucun SDK, service ou fournisseur n'est chargé.
        """
        self._providers = {
            (category, name): Provider(category, name)
            for category, name in KNOWN_PROVIDERS.items()
        }

    def register(self, category: str, name: str, factory: ToolFactory) -> None:
        """Enregistrer explicitement un adaptateur Python de confiance.

        Args:
            category: catégorie du contrat tools.yaml.
            name: identifiant du fournisseur, sans interprétation comme module.
            factory: fonction recevant une ToolDeclaration ; ses imports lourds
                doivent rester dans son corps pour être différés.
        Returns:
            None.
        Raises:
            ToolUnknownError: catégorie inconnue.
            ValueError: nom vide, factory invalide ou adaptateur déjà enregistré.
        Side effects:
            Modifie uniquement ce registre ; n'appelle jamais factory.
        """
        if category not in KNOWN_PROVIDERS:
            raise ToolUnknownError("catégorie d'outil inconnue")
        if not isinstance(name, str) or not name.strip() or not callable(factory):
            raise ValueError("nom non vide et factory appelable requis")
        existing = self._providers.get((category, name))
        if existing is not None and existing.factory is not None:
            raise ValueError("un adaptateur est déjà enregistré pour ce fournisseur")
        self._providers[category, name] = Provider(category, name, factory)

    def resolve(self, category: str, name: str) -> Provider:
        """Trouver un fournisseur sans l'initialiser.

        Args:
            category: catégorie déclarée.
            name: identifiant exact du fournisseur.
        Returns:
            Provider ; factory=None indique une intégration non implémentée.
        Raises:
            ToolUnknownError: paire catégorie/fournisseur inconnue.
        Side effects:
            Aucun, notamment aucun import de SDK ni accès réseau.
        """
        provider = self._providers.get((category, name))
        if provider is None:
            raise ToolUnknownError(
                "catégorie ou fournisseur non enregistré",
                hint="enregistrer explicitement un adaptateur Python autorisé",
            )
        return provider

    def initialize(self, declarations: Iterable[ToolDeclaration]) -> dict[str, object]:
        """Initialiser les seuls outils activés, dans l'ordre des déclarations.

        Args:
            declarations: sélections validées par load_configuration().
        Returns:
            Instances indexées par catégorie ; dictionnaire vide si tout est désactivé.
        Raises:
            ToolUnknownError: fournisseur inconnu, même désactivé.
            ToolUnavailableError: outil activé sans adaptateur ou dépendance.
            ToolInitializationError: échec d'une factory ou catégorie dupliquée.
        Side effects:
            Appelle les factories actives après vérification de tout le catalogue.
            Une factory peut avoir des effets externes ; aucun rollback automatique
            n'est garanti si une factory ultérieure échoue. Aucun résultat n'est caché.
        """
        active: list[tuple[ToolDeclaration, ToolFactory]] = []
        seen: set[str] = set()
        for declaration in declarations:
            if declaration.category in seen:
                raise ToolInitializationError("catégorie déclarée plusieurs fois")
            seen.add(declaration.category)
            provider = self.resolve(declaration.category, declaration.provider)
            if not declaration.enabled:
                continue
            if provider.factory is None:
                raise ToolUnavailableError(
                    "outil activé sans adaptateur implémenté",
                    config_path=f"$.tools.{declaration.category}",
                    hint="désactiver l'outil ou enregistrer son adaptateur",
                )
            active.append((declaration, provider.factory))
        return {item.category: _initialize(factory, item) for item, factory in active}


def _initialize(factory: ToolFactory, declaration: ToolDeclaration) -> object:
    try:
        return factory(declaration)
    except ImportError:
        # Les messages des SDK peuvent contenir des URLs avec identifiants.
        raise ToolUnavailableError(
            "dépendance de l'adaptateur indisponible",
            config_path=f"$.tools.{declaration.category}",
        ) from None
    except Exception:
        raise ToolInitializationError(
            "échec d'initialisation de l'adaptateur",
            config_path=f"$.tools.{declaration.category}",
        ) from None
