"""Suivi MLflow local, sans session globale ni serveur distant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mlflow.tracking import MlflowClient
# Le backend SQL est optionnel dans mlflow-skinny. Vérifier sa disponibilité au
# même moment que le SDK pour obtenir ToolUnavailableError via le registre.
import sqlalchemy  # noqa: F401
import alembic  # noqa: F401

from signalflow.ml.errors import TrackingError


class MLflowTracker:
    """Client par adaptateur ; la sélection YAML ne peut pas choisir une URI réseau.

    Une URI explicite peut etre fournie par le code (facade Ollama) : elle ne
    provient jamais de tools.yaml et reste validee par l'appelant.
    """

    def __init__(self, tracking_uri: str | None = None) -> None:
        """Conserver une URI optionnelle fournie par le code, sinon locale."""
        self.tracking_uri = tracking_uri
        self.client: MlflowClient | None = None

    def start(self, settings: dict[str, Any], output: Path) -> str:
        """Créer une expérience locale si nécessaire et démarrer un run.

        Args:
            settings: experiment_name et run_name validés.
            output: dossier de sortie absolu fourni explicitement par l'appelant.
        Returns:
            Identifiant du run MLflow créé, au statut RUNNING.
        Raises:
            TrackingError: backend, expérience ou création de run indisponibles.
        Side effects:
            Crée output/tracking, sa base SQLite et le dossier d'artefacts.
            Ne modifie pas la tracking URI globale de MLflow.
        """
        try:
            tracking = output / "tracking"
            tracking.mkdir(parents=True, exist_ok=True)
            # Un serveur HTTP choisit son stockage d'artefacts ; une base SQLite,
            # même fournie par le code, doit rester relative au dossier de sortie.
            server = self.tracking_uri is not None and self.tracking_uri.startswith(("http://", "https://"))
            uri = self.tracking_uri or f"sqlite:///{tracking / 'mlflow.db'}"
            self.client = MlflowClient(tracking_uri=uri)
            experiment = self.client.get_experiment_by_name(settings["experiment_name"])
            expected = None if server else (tracking / "artifacts").as_uri()
            experiment_id = experiment.experiment_id if experiment else self.client.create_experiment(
                settings["experiment_name"], artifact_location=expected,
            )
            # Avec un backend fichier, ne pas réutiliser une expérience redirigée ailleurs.
            experiment = self.client.get_experiment(experiment_id)
            if not server and experiment.artifact_location != expected:
                raise TrackingError("emplacement des artefacts incompatible avec le stockage local")
            run = self.client.create_run(experiment_id, tags={"mlflow.runName": settings["run_name"]})
            return run.info.run_id
        except TrackingError:
            raise
        except Exception:
            raise TrackingError("impossible de démarrer le suivi MLflow local") from None

    def log(
        self, run_id: str, parameters: dict[str, Any], metrics: dict[str, float], artifacts: Path,
    ) -> None:
        """Enregistrer les paramètres, mesures et fichiers d'un run actif.

        Args:
            run_id: identifiant retourné par start.
            parameters: hyperparamètres et seed, sans credentials.
            metrics: mesures numériques finies.
            artifacts: dossier contenant modèle, configuration, indices et rapports.
        Returns:
            None.
        Raises:
            TrackingError: écriture de paramètres, métriques ou artefacts impossible.
        Side effects:
            Écrit dans SQLite et copie les artefacts dans le stockage local MLflow.
        """
        try:
            for name, value in parameters.items():
                self.client.log_param(run_id, name, value)
            for name, value in metrics.items():
                self.client.log_metric(run_id, name, value)
            self.client.log_artifacts(run_id, str(artifacts))
        except Exception:
            raise TrackingError("échec d'enregistrement des résultats MLflow") from None

    def log_observations(self, run_id: str, metrics: dict[str, float], files: list[Path]) -> None:
        """Publier la télémétrie finale sans recopier les gros artefacts.

        Args: run_id: run existant, éventuellement terminé ; metrics: mesures ;
            files: fichiers de rapports à copier.
        Retourne None. Lève TrackingError en cas d'écriture impossible.
        Effets : écrit les métriques préfixées monitoring. et les rapports locaux.
        """
        try:
            for name, value in metrics.items():
                self.client.log_metric(run_id, f"monitoring.{name}", value)
            for path in files:
                self.client.log_artifact(run_id, str(path))
        except Exception:
            raise TrackingError("échec de publication des mesures locales") from None

    def finish(self, run_id: str, status: str = "FINISHED") -> None:
        """Terminer un run avec son statut explicite.

        Args:
            run_id: identifiant actif.
            status: FINISHED ou FAILED, défini par l'orchestrateur.
        Returns:
            None.
        Raises:
            TrackingError: mise à jour du backend impossible.
        Side effects:
            Met à jour le statut et la date de fin dans MLflow.
        """
        try:
            self.client.set_terminated(run_id, status=status)
        except Exception:
            raise TrackingError("impossible de terminer le run MLflow") from None
