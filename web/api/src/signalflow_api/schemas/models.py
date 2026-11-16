"""Enveloppes Pydantic et documentation JSON Schema sans recopier les règles ML."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema
from .catalog import component_schema


class TrainingPayload(BaseModel):
    """Spécification HTTP ; la validation métier est déléguée au moteur Python."""

    model_config = ConfigDict(extra='forbid', strict=True)
    training: Annotated[dict[str, Any], WithJsonSchema(component_schema('training'))]
    dataset: Annotated[dict[str, Any], WithJsonSchema(component_schema('dataset'))]
    tracking: Annotated[dict[str, Any], WithJsonSchema(component_schema('tracking'))]
    monitoring: Annotated[dict[str, Any] | None, WithJsonSchema({
        'anyOf': [component_schema('monitoring'), {'type': 'null'}],
    })] = None


class ErrorDetail(BaseModel):
    """Emplacement expurgé et règle violée, sans valeur rejetée."""

    path: str
    rule: str


class ErrorResponse(BaseModel):
    """Erreur stable corrélée à un identifiant créé par le serveur."""

    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str


class ValidationResponse(BaseModel):
    """Validation réussie ; ne signifie ni entraînement ni lecture d'un dataset."""

    valid: Literal[True] = True
    configuration: TrainingPayload


class HealthResponse(BaseModel):
    """État du processus API, sans prétendre vérifier des services absents."""

    status: Literal['ok'] = 'ok'
    service: Literal['signalflow-api'] = 'signalflow-api'
    mode: Literal['local-anonymous', 'authentication-required']
    training_execution: Literal[False] = False


class Algorithm(BaseModel):
    """Algorithme implémenté par le moteur et paramètres issus de son schéma."""

    id: str
    framework: str
    task: str
    parameters: dict[str, Any]


class AlgorithmsResponse(BaseModel):
    """Catalogue réel, distinct de la disponibilité d'un service d'exécution."""

    algorithms: list[Algorithm]
    execution_available: Literal[False] = False
