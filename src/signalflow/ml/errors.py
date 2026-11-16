"""Erreurs explicites du pipeline minimal, compatibles avec ConfigError."""

from signalflow.config.loader.errors import ConfigError


class PipelineError(ConfigError):
    """Exécution ou contrat métier du pipeline incorrect."""

    code = "PIPELINE_ERROR"


class DatasetError(PipelineError):
    """Données absentes, non numériques ou impossibles à séparer."""

    code = "DATASET_ERROR"


class TrainingError(PipelineError):
    """L'estimateur n'a pas pu être entraîné correctement."""

    code = "TRAINING_ERROR"


class TrackingError(PipelineError):
    """Échec de suivi local MLflow."""

    code = "TRACKING_ERROR"
