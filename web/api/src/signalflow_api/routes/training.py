"""Contrats HTTP versionnés : lecture et validation, aucune exécution de jobs."""
from typing import Any

from fastapi import APIRouter, Depends, Request
from ..dependencies import require_permission
from ..errors import ApiError
from ..schemas.catalog import component_schema
from ..schemas.models import (
    Algorithm, AlgorithmsResponse, HealthResponse, TrainingPayload,
    ValidationResponse, YamlExportResponse, YamlImportRequest,
)
from ..services.training import export_yaml, import_yaml, training_schema, validate_payload

router = APIRouter()


@router.get('/health', response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Vérifier uniquement la disponibilité du processus API."""
    return HealthResponse()


@router.get('/training/schema', dependencies=[Depends(require_permission('training:read'))])
def schema() -> dict[str, Any]:
    """Fournir le JSON Schema utilisable par un éditeur de configuration."""
    return training_schema()


@router.get('/algorithms', response_model=AlgorithmsResponse,
            dependencies=[Depends(require_permission('training:read'))])
def algorithms() -> AlgorithmsResponse:
    """Lister le seul estimateur actuellement implémenté et ses paramètres réels."""
    model = component_schema('training')['properties']['model']['properties']
    return AlgorithmsResponse(algorithms=[Algorithm(id=model['type']['const'], framework='scikit-learn',
        task='classification', parameters=model['parameters'])])


@router.post('/training/validate', response_model=ValidationResponse, response_model_exclude_unset=True,
             dependencies=[Depends(require_permission('training:validate'))])
def validate(payload: TrainingPayload) -> ValidationResponse:
    """Valider la spécification sans entraînement, écriture ni création d'expérience."""
    return ValidationResponse(valid=True, configuration=validate_payload(payload))


@router.post('/training/import', response_model=ValidationResponse, response_model_exclude_unset=True,
             dependencies=[Depends(require_permission('training:validate'))])
def import_configuration(request: YamlImportRequest) -> ValidationResponse:
    """Valider un YAML importé, sans écriture ni lecture de fichier système."""
    return ValidationResponse(valid=True, configuration=import_yaml(request))


@router.post('/training/export', response_model=YamlExportResponse,
             dependencies=[Depends(require_permission('training:read'))])
def export_configuration(payload: TrainingPayload) -> YamlExportResponse:
    """Produire un YAML canonique sous contrôle serveur."""
    return export_yaml(payload)


@router.post('/trainings', status_code=501, dependencies=[Depends(require_permission('training:create'))])
def create_training(payload: TrainingPayload) -> None:
    """Réserver le contrat de création ; aucun identifiant, stockage ou job n'est créé."""
    validate_payload(payload)
    raise ApiError(501, 'TRAINING_EXECUTION_NOT_IMPLEMENTED',
                   "La création d'entraînements sera disponible avec le service de jobs durables.")
