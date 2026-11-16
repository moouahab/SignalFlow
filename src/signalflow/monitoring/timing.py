"""Interface minimale injectable dans n'importe quel moteur d'entraînement."""

from contextlib import AbstractContextManager, contextmanager
from collections.abc import Iterator
from typing import Protocol


class Instrumentation(Protocol):
    """Extension possible sans dépendance à sklearn, MLflow, CPU ou GPU."""

    def stage(self, name: str) -> AbstractContextManager[None]:
        """Mesurer un bloc nommé ; retourne un contexte, sans absorber ses erreurs.

        Args: name: identifiant stable de l'étape, sans donnée utilisateur.
        Effets: propres à l'implémentation ; exceptions du bloc propagées.
        """
        ...


class NullInstrumentation:
    """Instrumentation désactivée : aucun thread, horloge ou import psutil."""

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Retourner un contexte sans mesure pour name ; propage les exceptions.

        Aucun effet de bord ; ne modifie pas le bloc exécuté.
        """
        yield
