"""Valeurs indépendantes des fichiers YAML et contexte d'exécution local de confiance."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .errors import PipelineError


@dataclass(frozen=True)
class TrainingSpec:
    """Spécification validée par validate_training_spec, stockée sans référence mutable.

    Le JSON interne évite de partager des dictionnaires avec l'appelant. Utiliser
    le validateur pour construire cette valeur ; le runner la revalide à sa frontière.
    """

    _json: str = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Retourner une copie profonde JSON ; une valeur forgée invalide lève PipelineError."""
        try:
            document = json.loads(self._json)
            if not isinstance(document, dict):
                raise ValueError
            return document
        except (ValueError, TypeError, RecursionError):
            raise PipelineError("spécification sérialisée invalide") from None


@dataclass(frozen=True)
class ExecutionContext:
    """Chemins choisis par l'opérateur Python, jamais par un futur client HTTP.

    dataset_root est obligatoire pour un CSV ; output_dir reçoit les artefacts et
    le suivi SQLite. Aucun dossier n'est créé à la construction. Les chemins relatifs
    sont résolus à la construction pour rendre le contexte indépendant d'un chdir.
    """

    output_dir: Path | str
    dataset_root: Path | str | None = None
    orchestration: str = "local"

    def __post_init__(self) -> None:
        """Résoudre les chemins sans effet d'écriture ; erreurs traduites en PipelineError."""
        if self.orchestration not in {"local", "zenml"}:
            raise PipelineError("orchestration inconnue")
        try:
            object.__setattr__(self, "output_dir", Path(self.output_dir).expanduser().resolve())
            if self.dataset_root is not None:
                object.__setattr__(self, "dataset_root", Path(self.dataset_root).expanduser().resolve())
        except (OSError, RuntimeError, TypeError, ValueError):
            raise PipelineError("contexte d'exécution invalide") from None


@dataclass(frozen=True)
class TrainingReport:
    """Résultat local sérialisable, incluant les chemins d'artefacts comme la CLI.

    Ce contrat Python n'est pas un DTO HTTP : une future API devra masquer les
    chemins locaux. La représentation interne ne partage aucun dictionnaire mutable.
    """

    _json: str = field(repr=False)

    @classmethod
    def from_dict(cls, report: dict[str, Any]) -> TrainingReport:
        """Capturer le rapport du moteur ; aucune lecture de fichier ni de SDK."""
        return cls(json.dumps(report, allow_nan=False, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        """Retourner une copie indépendante du rapport pour export ou inspection."""
        return json.loads(self._json)

    @property
    def run_id(self) -> str:
        """Identifiant du run MLflow effectivement créé."""
        return self.to_dict()["run_id"]

    @property
    def status(self) -> str:
        """Statut final du moteur ; les erreurs restent des exceptions typées."""
        return self.to_dict()["status"]

    @property
    def metrics(self) -> dict[str, float]:
        """Copie des métriques ML, sans les mesures techniques de monitoring."""
        return self.to_dict()["metrics"]
