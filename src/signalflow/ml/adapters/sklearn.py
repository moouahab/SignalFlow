"""Adaptateur réel sklearn : importé uniquement par la factory activée."""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from signalflow.ml.errors import TrainingError
from signalflow.monitoring import Instrumentation, NullInstrumentation
from .data import Dataset, Split, load_dataset, split_dataset


@dataclass(frozen=True)
class TrainingResult:
    """Estimateur complet ajusté, mesures et données nécessaires à l'audit."""

    model: Pipeline
    metrics: dict[str, float]
    duration_seconds: float
    dataset: Dataset
    split: Split


class SklearnTrainer:
    """Classification numérique via imputation, standardisation et logistique."""

    def train(
        self, training: dict[str, Any], dataset_config: dict[str, Any],
        dataset_path: Path | None = None,
        *, instrumentation: Instrumentation | None = None,
    ) -> TrainingResult:
        """Charger, séparer, ajuster sur train puis évaluer validation et test.

        Args:
            training: seed et paramètres validés de la régression logistique.
            dataset_config: source et fractions validées.
            dataset_path: chemin CSV déjà confiné, sinon None.
            instrumentation: contextes de mesure indépendants du framework, facultatifs.
        Returns:
            TrainingResult avec huit métriques macro/accuracy et durée du fit.
        Raises:
            DatasetError: données ou découpage invalides.
            TrainingError: erreur sklearn ou non-convergence du modèle.
            ConfigError: lecture du CSV impossible.
        Side effects:
            Lit éventuellement le CSV ; entraîne un modèle en mémoire, sans écriture.
            Limite temporairement les pools BLAS à un thread pour la reproductibilité.
        """
        observer = instrumentation if instrumentation is not None else NullInstrumentation()
        with threadpool_limits(limits=1), observer.stage("dataset_load"):
            dataset = load_dataset(dataset_config["source"], dataset_path)
        with observer.stage("data_split"):
            split = split_dataset(dataset, dataset_config["split"], training["seed"])
        model = Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(
                **training["model"]["parameters"], random_state=training["seed"], solver="lbfgs",
            )),
        ])
        try:
            with threadpool_limits(limits=1), warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                start = time.perf_counter()
                # Aucun fit sur validation/test : même pipeline pour les prédictions.
                # Les composants restent ceux du pipeline sauvegardé. Séparer les
                # deux fit permet leur mesure sans réapprendre ni recopier le dataset.
                with observer.stage("preprocessing"):
                    transformed = model[:-1].fit_transform(dataset.X[split.train], dataset.y[split.train])
                with observer.stage("model_fit"):
                    model[-1].fit(transformed, dataset.y[split.train])
                duration = time.perf_counter() - start
                metrics = {}
                with observer.stage("predict_evaluate"):
                    for name in ("validation", "test"):
                        indices = getattr(split, name)
                        metrics.update(_evaluate(model, dataset, indices, name))
        except (ValueError, ConvergenceWarning):
            raise TrainingError(
                "entraînement invalide ou modèle non convergé",
                hint="vérifier les données et augmenter max_iter en cas de non-convergence",
            ) from None
        return TrainingResult(model, metrics, duration, dataset, split)

    def save(self, result: TrainingResult, destination: Path) -> Path:
        """Sauvegarder le pipeline complet, prétraitement compris, avec joblib.

        Args:
            result: résultat d'un entraînement terminé.
            destination: dossier de run existant et réservé par l'orchestrateur.
        Returns:
            Chemin du fichier model.joblib.
        Raises:
            OSError: écriture impossible.
        Side effects:
            Écrit model.joblib ; ce fichier ne doit être rechargé que s'il est fiable.
        """
        path = destination / "model.joblib"
        joblib.dump(result.model, path)
        return path


def _evaluate(model: Pipeline, dataset: Dataset, indices: Any, prefix: str) -> dict[str, float]:
    expected = dataset.y[indices]
    predicted = model.predict(dataset.X[indices])
    precision, recall, f1, _ = precision_recall_fscore_support(
        expected, predicted, average="macro", labels=model.classes_, zero_division=0,
    )
    return {
        f"{prefix}_accuracy": float(accuracy_score(expected, predicted)),
        f"{prefix}_precision_macro": float(precision),
        f"{prefix}_recall_macro": float(recall),
        f"{prefix}_f1_macro": float(f1),
    }
