"""Contrats HTTP versionnés : lecture et validation, aucune exécution de jobs."""
from typing import Any

from fastapi import APIRouter, Depends, Request
from ..dependencies import require_permission
from ..errors import ApiError
from ..schemas.catalog import component_schema
from ..schemas.models import Algorithm, AlgorithmsResponse, HealthResponse, TrainingPayload, ValidationResponse
from ..services.training import training_schema, validate_payload

router = APIRouter()


@router.get('/health', response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Vérifier uniquement la disponibilité du processus API."""
    return HealthResponse(mode='authentication-required' if request.app.state.settings.require_authentication
                          else 'local-anonymous')


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


@router.post('/trainings', status_code=501, dependencies=[Depends(require_permission('training:create'))])
def create_training(payload: TrainingPayload) -> None:
    """Réserver le contrat de création ; aucun identifiant, stockage ou job n'est créé."""
    validate_payload(payload)
    raise ApiError(501, 'TRAINING_EXECUTION_NOT_IMPLEMENTED',
                   "La création d'entraînements sera disponible avec le service de jobs durables.")
